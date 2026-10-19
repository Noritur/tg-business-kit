"""The command menu: what Telegram is told, and the commands themselves."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from telegram import Bot

from kit.app import Deps, build_app
from kit.commands import menu_commands, register_menu
from kit.profile import Modules
from kit.storage import MemoryLeadStore
from kit.web import create_app
from tests.conftest import ADMIN_ID, CLIENT_ID, TOKEN, FakeTelegramRequest


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def menus(request: FakeTelegramRequest) -> dict[str, list[str]]:
    """setMyCommands calls as {scope type: [command, ...]}."""
    out = {}
    for endpoint, params in request.calls:
        if endpoint == "setMyCommands":
            scope = _decoded(params.get("scope")) or {"type": "default"}
            out[scope["type"]] = [c["command"] for c in _decoded(params["commands"])]
    return out


async def _bot_api() -> tuple[Bot, FakeTelegramRequest]:
    request = FakeTelegramRequest()
    bot = Bot(TOKEN, request=request, get_updates_request=FakeTelegramRequest())
    await bot.initialize()
    return bot, request


def test_client_menu_follows_the_enabled_modules(dental):
    assert [c.command for c in menu_commands(dental)] == ["start", "book", "prices", "faq"]
    no_faq = dental.model_copy(update={"modules": Modules(faq=False)})
    assert [c.command for c in menu_commands(no_faq)] == ["start", "book", "prices"]
    owner = [c.command for c in menu_commands(dental, owner=True)]
    assert owner == ["start", "book", "prices", "faq", "leads"]


def test_descriptions_are_in_the_profile_language(school):
    assert menu_commands(school)[0].description == "Головне меню"


async def test_register_sets_the_client_menu_and_the_owner_menu_in_their_chat(dental):
    bot, request = await _bot_api()
    await register_menu(bot, dental, ADMIN_ID)
    assert menus(request) == {
        "default": ["start", "book", "prices", "faq"],
        "chat": ["start", "book", "prices", "faq", "leads"],
    }
    scopes = [_decoded(p["scope"]) for e, p in request.calls if e == "setMyCommands"]
    [owner_scope] = [scope for scope in scopes if scope["type"] == "chat"]
    assert owner_scope["chat_id"] == ADMIN_ID


async def test_without_an_owner_only_the_client_menu_is_set(dental):
    bot, request = await _bot_api()
    await register_menu(bot, dental, 0)
    assert list(menus(request)) == ["default"]


async def test_a_refused_menu_is_logged_and_does_not_stop_the_bot(dental, caplog):
    bot, request = await _bot_api()
    request.errors["setMyCommands"] = "Bad Request: chat not found"
    with caplog.at_level(logging.ERROR):
        await register_menu(bot, dental, ADMIN_ID)  # must not raise
    assert "menu not set" in caplog.text


async def test_polling_start_registers_the_menu(dental):
    request = FakeTelegramRequest()
    deps = Deps(profile=dental, store=MemoryLeadStore(), answerer=None, admin_id=ADMIN_ID)
    app = build_app(deps, TOKEN, request=request, get_updates_request=FakeTelegramRequest())
    await app.initialize()
    await app.post_init(app)  # what run_polling() calls after initialize()
    await app.shutdown()
    assert "default" in menus(request) and "chat" in menus(request)


def test_webhook_start_registers_the_menu(dental):
    request = FakeTelegramRequest()
    deps = Deps(profile=dental, store=MemoryLeadStore(), answerer=None, admin_id=ADMIN_ID)
    api = create_app(deps, TOKEN, "secret", request, FakeTelegramRequest())
    with TestClient(api):
        pass
    assert "default" in menus(request) and "chat" in menus(request)


@pytest.mark.parametrize(
    ("command", "text_start", "first_button"),
    [
        ("/book", "What do you need?", "b:s:chk"),
        ("/prices", "Services and prices:", "b:svc"),
        ("/faq", "Frequently asked", "f:0"),
    ],
)
async def test_menu_commands_open_their_screen_as_a_new_message(
    bot, command, text_start, first_button
):
    await bot.send(bot.text(command))
    [sent] = [p for p in bot.calls("sendMessage") if p["chat_id"] == CLIENT_ID]
    assert sent["text"].startswith(text_start)
    assert bot.last_buttons()[0] == first_button
    assert bot.calls("editMessageText") == []


async def test_a_disabled_module_has_no_command(make_bot, dental):
    bot = await make_bot(dental.model_copy(update={"modules": Modules(faq=False)}))
    await bot.send(bot.text("/faq"))
    # No handler: the command falls through and nothing is sent.
    assert bot.calls("sendMessage") == []
