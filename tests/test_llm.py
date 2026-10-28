import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest
from google.genai import errors

import kit.llm as llm

# Imported by name, so the fixtures that patch kit.llm leave these at their real values.
from kit.llm import (
    BACKOFF_SECONDS,
    CALL_TIMEOUT_SECONDS,
    MODELS,
    RETRIES_PER_MODEL,
    Answer,
    GeminiAnswerer,
    build_system_prompt,
)
from kit.storage import STALE_AFTER
from tests.conftest import metric

HANG = object()  # a call that does not return in time, like a stalled connection


class FakeModels:
    """Plays back one outcome per call: a dict (sent as JSON), raw text, an exception, or HANG.

    HANG stalls for seconds and then fails the test, so a missing timeout shows up
    as a failure instead of a suite that never ends.
    """

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models_called: list[str] = []

    async def generate_content(self, model, contents, config=None):
        self.models_called.append(model)
        outcome = self.outcomes.pop(0)
        if outcome is HANG:
            await asyncio.sleep(5)
            raise AssertionError("a hung call was not cut off")
        if isinstance(outcome, Exception):
            raise outcome
        text = json.dumps(outcome) if isinstance(outcome, dict) else outcome
        return SimpleNamespace(text=text)


def _answerer(outcomes) -> tuple[GeminiAnswerer, FakeModels]:
    models = FakeModels(outcomes)
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return GeminiAnswerer("unused", models=["m1", "m2", "m3"], client=client), models


def _api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(llm, "BACKOFF_SECONDS", 0)


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch):
    monkeypatch.setattr(llm, "CALL_TIMEOUT_SECONDS", 0.05)


OK = {"reply": "We accept Bupa.", "handoff": False}


async def test_answer_is_parsed(dental):
    answerer, models = _answerer([OK])
    assert await answerer.answer(dental, "bupa?") == Answer(**OK)
    assert models.models_called == ["m1"]


async def test_rate_limit_moves_to_the_next_model_at_once(dental):
    answerer, models = _answerer([_api_error(429), OK])
    calls = [("m1", "rate_limited"), ("m2", "ok"), ("m1", "ok")]
    before = [metric("kit_llm_requests_total", model=m, outcome=o) for m, o in calls]
    assert (await answerer.answer(dental, "bupa?")).reply == "We accept Bupa."
    assert models.models_called == ["m1", "m2"]
    after = [metric("kit_llm_requests_total", model=m, outcome=o) for m, o in calls]
    assert [a - b for a, b in zip(after, before, strict=True)] == [1, 1, 0]


@pytest.mark.parametrize(
    ("outcomes", "counted"),
    [
        ([_api_error(503), OK], ["overloaded", "ok"]),
        (["not json", OK], ["bad_schema"]),  # then m2 answers
        ([_api_error(400)], ["error"]),
        ([ConnectionResetError("connection reset by peer")], ["error"]),
        ([HANG, OK], ["timeout"]),  # then m2 answers
    ],
    ids=["overloaded", "bad schema", "other API error", "network error", "timeout"],
)
async def test_every_call_is_counted_once_by_outcome(dental, outcomes, counted):
    answerer, _ = _answerer(outcomes)
    names = ("ok", "overloaded", "bad_schema", "timeout", "error")
    before = {o: metric("kit_llm_requests_total", model="m1", outcome=o) for o in names}
    with contextlib.suppress(Exception):  # some cases end in an error, on purpose
        await answerer.answer(dental, "bupa?")
    after = {o: metric("kit_llm_requests_total", model="m1", outcome=o) for o in names}
    assert {o: after[o] - before[o] for o in names} == {o: counted.count(o) for o in names}


async def test_overload_is_retried_on_the_same_model(dental):
    answerer, models = _answerer([_api_error(503), OK])
    await answerer.answer(dental, "bupa?")
    assert models.models_called == ["m1", "m1"]


async def test_output_outside_the_schema_moves_on(dental):
    answerer, models = _answerer(["Sure! We accept Bupa.", OK])
    await answerer.answer(dental, "bupa?")
    assert models.models_called == ["m1", "m2"]


async def test_a_hung_call_is_cut_off_and_the_next_model_answers(dental):
    answerer, models = _answerer([HANG, OK])
    assert (await answerer.answer(dental, "bupa?")).reply == "We accept Bupa."
    assert models.models_called == ["m1", "m2"]


async def test_every_model_hung_raises(dental):
    answerer, models = _answerer([HANG] * 3)
    with pytest.raises(RuntimeError, match="all models failed") as caught:
        await answerer.answer(dental, "bupa?")
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert models.models_called == ["m1", "m2", "m3"]


def test_the_longest_fallback_run_ends_well_inside_the_claim_window():
    # Every attempt on every model runs to the timeout and is followed by its backoff.
    backoff = sum(BACKOFF_SECONDS * (attempt + 1) for attempt in range(RETRIES_PER_MODEL))
    longest = len(MODELS) * (RETRIES_PER_MODEL * CALL_TIMEOUT_SECONDS + backoff)
    assert longest < STALE_AFTER.total_seconds() / 2


async def test_other_api_errors_are_not_retried(dental):
    answerer, models = _answerer([_api_error(400)])
    with pytest.raises(errors.APIError):
        await answerer.answer(dental, "bupa?")
    assert models.models_called == ["m1"]


async def test_every_model_exhausted_raises(dental):
    answerer, _ = _answerer([_api_error(429)] * 3)
    with pytest.raises(RuntimeError, match="all models failed"):
        await answerer.answer(dental, "bupa?")


def test_prompt_holds_the_facts_and_the_rules_in_the_profile_language(dental, school):
    prompt = build_system_prompt(dental)
    assert "Check-up + hygiene cleaning: 60 GBP" in prompt
    assert "Do you accept dental insurance?" in prompt
    assert "Answer in English" in prompt
    assert "Відповідай українською" in build_system_prompt(school)
