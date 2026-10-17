"""Updates the bot must ignore without crashing and without answering."""

import logging

import pytest

QUIET_UPDATES = {
    "edited message": lambda bot: bot.raw("edited_message", text="hello again"),
    "photo without text": lambda bot: bot.raw("message", photo=[]),
    "channel post": lambda bot: bot.raw("channel_post", text="news"),
    "group chatter": lambda bot: bot.text("hi all", chat_type="group"),
    "edited command": lambda bot: bot.raw("edited_message", text="/start"),
}

QUIET_CLICKS = ["b:x:chk", "b:s:nope", "f:99", "f:²", "m:admin", "zzz", ""]


def _no_screens(bot):
    return bot.calls("sendMessage") == [] and bot.calls("editMessageText") == []


@pytest.mark.parametrize("case", QUIET_UPDATES, ids=str)
async def test_update_is_ignored(bot, case, caplog):
    with caplog.at_level(logging.ERROR):
        await bot.send(QUIET_UPDATES[case](bot))
    assert _no_screens(bot)
    assert "handler failed" not in caplog.text


@pytest.mark.parametrize("data", QUIET_CLICKS)
async def test_garbage_button_is_acknowledged_and_ignored(bot, data, caplog):
    with caplog.at_level(logging.ERROR):
        await bot.send(bot.click(data))
    assert _no_screens(bot)
    assert "handler failed" not in caplog.text
