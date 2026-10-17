import logging
from datetime import timedelta

from kit.llm import Answer
from kit.profile import Modules
from tests.conftest import ADMIN_ID, CLIENT_ID, MONDAY_NOON, new_lead, put


async def test_answer_comes_back_with_booking_buttons(bot, answerer):
    answerer.outcome = Answer(reply="We accept Bupa, Cigna and Aviva.", handoff=False)
    await bot.send(bot.text("do you take bupa?"))

    assert answerer.questions == ["do you take bupa?"]
    [reply] = bot.texts_to(CLIENT_ID)
    assert reply == "We accept Bupa, Cigna and Aviva."
    assert bot.last_buttons() == ["b:svc", "m:home"]
    assert bot.texts_to(ADMIN_ID) == []


async def test_unknown_question_is_forwarded_to_the_owner(bot, answerer):
    answerer.outcome = Answer(reply="I'm passing this to the team.", handoff=True)
    await bot.send(bot.text("can I bring my dog?"))

    [owner_note] = bot.texts_to(ADMIN_ID)
    assert "can I bring my dog?" in owner_note
    assert bot.texts_to(CLIENT_ID) == ["I'm passing this to the team."]


async def test_llm_failure_still_answers_and_forwards(bot, answerer, caplog):
    answerer.outcome = RuntimeError("all models failed")
    with caplog.at_level(logging.ERROR):
        await bot.send(bot.text("are you open on Sunday?"))

    [reply] = bot.texts_to(CLIENT_ID)
    assert reply.startswith("Thanks for the question")
    assert "are you open on Sunday?" in bot.texts_to(ADMIN_ID)[0]
    assert "LLM failed" in caplog.text


async def test_phone_after_booking_is_attached_not_sent_to_the_llm(bot, store, answerer, dental):
    an_hour_ago = MONDAY_NOON - timedelta(hours=1)
    await put(store, new_lead(dental, tg_user_id=CLIENT_ID, created_at=an_hour_ago))
    await bot.send(bot.text("+44 7700 900123"))

    [lead] = await store.recent_leads(dental.slug)
    assert lead.phone == "+447700900123"
    assert answerer.questions == []
    assert bot.texts_to(CLIENT_ID) == ["Number saved, thank you. We'll call to confirm."]
    assert "+447700900123" in bot.texts_to(ADMIN_ID)[0]


async def test_phone_without_a_recent_booking_goes_to_the_llm(bot, store, answerer, dental):
    old = new_lead(dental, tg_user_id=CLIENT_ID, created_at=MONDAY_NOON - timedelta(days=3))
    await put(store, old, capacity=1)
    await bot.send(bot.text("+44 7700 900123"))

    assert answerer.questions == ["+44 7700 900123"]
    [lead] = await store.recent_leads(dental.slug)
    assert lead.phone is None


async def test_ai_answers_off_means_every_question_goes_to_the_owner(make_bot, dental, answerer):
    profile = dental.model_copy(update={"modules": Modules(ai_answers=False)})
    bot = await make_bot(profile)
    await bot.send(bot.text("how much is whitening?"))

    assert answerer.questions == []
    assert "how much is whitening?" in bot.texts_to(ADMIN_ID)[0]
    assert bot.texts_to(CLIENT_ID)[0].startswith("Thanks for the question")
