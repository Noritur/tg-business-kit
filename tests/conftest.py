"""Shared fakes for the test suite.

Nothing here talks to the network: the Telegram Bot API and Gemini are replaced
at their boundaries, and a guard fails any test that tries to connect anywhere
but localhost. Updates still travel the real path: Update.de_json ->
Application.process_update -> handler registration -> handler code.
"""

import asyncio
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from telegram import Update
from telegram.request import BaseRequest

from kit.app import Deps, build_app
from kit.llm import Answer
from kit.profile import Profile, load_profile
from kit.storage import MemoryLeadStore, NewLead

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "123456:TEST-TOKEN"
ADMIN_ID = 999
CLIENT_ID = 111
# Monday 2026-10-19 12:00 UTC: 13:00 in London, 14:00 in Madrid.
MONDAY_NOON = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
BOT_USER = {
    "id": 1,
    "is_bot": True,
    "first_name": "Kit",
    "username": "kit_test_bot",
    "can_join_groups": False,
    "can_read_all_group_messages": False,
    "supports_inline_queries": False,
}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", None}


# --- network guard -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(f"network access blocked in tests: {address!r}")
        return real_connect(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(f"DNS lookup blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


# --- profiles and dependencies -----------------------------------------------


@pytest.fixture
def dental() -> Profile:
    return load_profile(ROOT / "presets" / "dental-clinic.json")


@pytest.fixture
def school() -> Profile:
    return load_profile(ROOT / "presets" / "language-school.json")


class FakeAnswerer:
    """`outcome` is an Answer to return or an exception to raise."""

    def __init__(self):
        self.outcome: Answer | Exception = Answer(reply="We accept Bupa.", handoff=False)
        self.questions: list[str] = []

    async def answer(self, profile, question):
        self.questions.append(question)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def store() -> MemoryLeadStore:
    return MemoryLeadStore()


@pytest.fixture
def answerer() -> FakeAnswerer:
    return FakeAnswerer()


def new_lead(profile: Profile, **overrides) -> NewLead:
    fields = {
        "client_slug": profile.slug,
        "tg_user_id": 222,
        "tg_username": "someone_else",
        "tg_name": "Someone",
        "service": profile.services[0].code,
        "service_label": profile.services[0].label,
        "slot_date": MONDAY_NOON.date() + timedelta(days=1),
        "slot_time": "09:00",
        "created_at": MONDAY_NOON - timedelta(hours=1),
    }
    return NewLead(**{**fields, **overrides})


async def put(store, lead: NewLead, capacity: int = 1):
    """Seeds a lead the way another client would have booked it."""
    return await store.save_lead(
        lead, capacity=capacity, max_per_client=100, today=MONDAY_NOON.date()
    )


# --- Telegram Bot API --------------------------------------------------------


class FakeTelegramRequest(BaseRequest):
    """Answers Bot API calls locally and records what the bot sent."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 1000
        # endpoint -> Bot API error description: that call answers 400 instead
        self.errors: dict[str, str] = {}

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self) -> float:
        return 5.0

    async def do_request(self, url, method, request_data=None, **_timeouts):
        await asyncio.sleep(0)  # real I/O yields to the event loop
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint in self.errors:
            body = {"ok": False, "error_code": 400, "description": self.errors[endpoint]}
            return 400, json.dumps(body).encode()
        return 200, json.dumps({"ok": True, "result": self._result(endpoint, params)}).encode()

    def _result(self, endpoint: str, params: dict):
        if endpoint == "getMe":
            return BOT_USER
        if endpoint in ("sendMessage", "editMessageText"):
            self._next_id += 1
            return {
                "message_id": params.get("message_id", self._next_id),
                "date": int(time.time()),
                "chat": {"id": params.get("chat_id", 0), "type": "private"},
                "from": BOT_USER,
                "text": params.get("text", "…"),
            }
        return True


def _buttons(params: dict) -> list[tuple[str, str]]:
    markup = params.get("reply_markup")
    if not markup:
        return []
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return [(b["text"], b.get("callback_data")) for row in markup["inline_keyboard"] for b in row]


class BotHarness:
    """Feeds update dicts through the real Application and reads the results."""

    def __init__(self, app, request: FakeTelegramRequest, deps: Deps):
        self.app, self.request, self.deps = app, request, deps
        self._update_id = 0

    async def send(self, update: dict) -> None:
        await self.app.process_update(Update.de_json(update, self.app.bot))

    def calls(self, endpoint: str) -> list[dict]:
        return [p for e, p in self.request.calls if e == endpoint]

    def texts_to(self, chat_id: int) -> list[str]:
        return [p["text"] for p in self.calls("sendMessage") if p.get("chat_id") == chat_id]

    def screens(self) -> list[dict]:
        """Everything the client saw: sent and edited messages, in order."""
        return [p for e, p in self.request.calls if e in ("sendMessage", "editMessageText")]

    def last_screen(self) -> tuple[str, list[tuple[str, str]]]:
        screen = [p for p in self.screens() if p.get("chat_id", CLIENT_ID) == CLIENT_ID][-1]
        return screen["text"], _buttons(screen)

    def last_buttons(self) -> list[str]:
        return [data for _, data in self.last_screen()[1]]

    # update builders

    def _uid(self) -> int:
        self._update_id += 1
        return self._update_id

    def _message(self, user_id: int, chat_type: str = "private", **fields) -> dict:
        return {
            "message_id": self._update_id,
            "date": int(time.time()),
            "chat": {"id": user_id, "type": chat_type},
            "from": _user(user_id),
            **fields,
        }

    def text(self, text: str, user_id: int = CLIENT_ID, chat_type: str = "private") -> dict:
        entities = {}
        if text.startswith("/"):
            entities = {"entities": [{"type": "bot_command", "offset": 0, "length": len(text)}]}
        return {
            "update_id": self._uid(),
            "message": self._message(user_id, chat_type, text=text, **entities),
        }

    def click(self, data: str, user_id: int = CLIENT_ID) -> dict:
        return {
            "update_id": self._uid(),
            "callback_query": {
                "id": f"cb{self._update_id}",
                "chat_instance": "ci",
                "from": _user(user_id),
                "data": data,
                "message": {
                    "message_id": 50,
                    "date": int(time.time()),
                    "chat": {"id": user_id, "type": "private"},
                    "from": BOT_USER,
                    "text": "previous bot answer",
                },
            },
        }

    def raw(self, kind: str, **fields) -> dict:
        """An update of any other kind, e.g. raw('edited_message', text='hi')."""
        return {"update_id": self._uid(), kind: self._message(CLIENT_ID, **fields)}


def _user(user_id: int) -> dict:
    return {
        "id": user_id,
        "is_bot": False,
        "first_name": "Olena",
        "username": "olena_client",
        "language_code": "uk",
    }


@pytest.fixture
async def make_bot(store, answerer):
    """make_bot(profile, **deps_overrides) -> BotHarness over the real build_app()."""
    apps = []

    async def _make(profile: Profile, **overrides) -> BotHarness:
        deps = Deps(
            profile=profile,
            store=overrides.pop("store", store),
            answerer=overrides.pop("answerer", answerer),
            admin_id=overrides.pop("admin_id", ADMIN_ID),
            clock=overrides.pop("clock", lambda: MONDAY_NOON),
        )
        request = FakeTelegramRequest()
        app = build_app(deps, TOKEN, request=request, get_updates_request=FakeTelegramRequest())
        await app.initialize()
        apps.append(app)
        return BotHarness(app, request, deps)

    yield _make
    for app in apps:
        await app.shutdown()


@pytest.fixture
async def bot(make_bot, dental) -> BotHarness:
    return await make_bot(dental)
