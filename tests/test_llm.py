import json
from types import SimpleNamespace

import pytest
from google.genai import errors

import kit.llm as llm
from kit.llm import Answer, GeminiAnswerer, build_system_prompt


class FakeModels:
    """Plays back one outcome per call: a dict (sent as JSON), raw text, or an exception."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models_called: list[str] = []

    async def generate_content(self, model, contents, config=None):
        self.models_called.append(model)
        outcome = self.outcomes.pop(0)
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


OK = {"reply": "We accept Bupa.", "handoff": False}


async def test_answer_is_parsed(dental):
    answerer, models = _answerer([OK])
    assert await answerer.answer(dental, "bupa?") == Answer(**OK)
    assert models.models_called == ["m1"]


async def test_rate_limit_moves_to_the_next_model_at_once(dental):
    answerer, models = _answerer([_api_error(429), OK])
    assert (await answerer.answer(dental, "bupa?")).reply == "We accept Bupa."
    assert models.models_called == ["m1", "m2"]


async def test_overload_is_retried_on_the_same_model(dental):
    answerer, models = _answerer([_api_error(503), OK])
    await answerer.answer(dental, "bupa?")
    assert models.models_called == ["m1", "m1"]


async def test_output_outside_the_schema_moves_on(dental):
    answerer, models = _answerer(["Sure! We accept Bupa.", OK])
    await answerer.answer(dental, "bupa?")
    assert models.models_called == ["m1", "m2"]


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
