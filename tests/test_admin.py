from tests.conftest import ADMIN_ID, CLIENT_ID, new_lead, put


async def test_start_shows_the_menu_of_enabled_modules(bot):
    await bot.send(bot.text("/start"))
    text, buttons = bot.last_screen()
    assert text.startswith("Hi, this is Bright Smile Dental")
    assert [data for _, data in buttons] == ["b:svc", "m:prices", "m:sched", "m:faq"]


async def test_leads_lists_bookings_for_the_owner(bot, store, dental):
    await put(store, new_lead(dental), capacity=1)
    await bot.send(bot.text("/leads", user_id=ADMIN_ID))
    [listing] = bot.texts_to(ADMIN_ID)
    assert "Check-up + hygiene cleaning" in listing and "@someone_else" in listing


async def test_leads_is_silent_for_everyone_else(bot, store, dental):
    await put(store, new_lead(dental), capacity=1)
    await bot.send(bot.text("/leads", user_id=CLIENT_ID))
    assert bot.calls("sendMessage") == []


async def test_leads_is_silent_when_no_owner_is_configured(make_bot, dental, store):
    await put(store, new_lead(dental), capacity=1)
    bot = await make_bot(dental, admin_id=0)
    await bot.send(bot.text("/leads", user_id=0))
    assert bot.calls("sendMessage") == []
