"""Free-text answers grounded in the business profile (Gemini).

The model sees only the profile and must return {reply, handoff}: handoff=true
means the facts did not cover the question, and the bot forwards it to the
owner instead of letting the model guess.

Fallback chain: 429 (the free-tier quota of one model is spent) moves to the
next model at once; 503 (overloaded) is retried with backoff. The async client
is used so a slow model never blocks other chats on the same event loop.
"""

import asyncio
import logging
from typing import Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from kit.profile import Profile

log = logging.getLogger(__name__)

MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.8-flash"]
RETRIES_PER_MODEL = 2
BACKOFF_SECONDS = 1.5
RATE_LIMITED = 429
OVERLOADED = 503


class Answer(BaseModel):
    reply: str
    handoff: bool


class Answerer(Protocol):
    async def answer(self, profile: Profile, question: str) -> Answer: ...


_RULES = {
    "uk": (
        "Правила:\n"
        "- Відповідай українською, коротко (2-4 речення), тепло і по суті.\n"
        "- Використовуй ТІЛЬКИ факти вище. Не вигадуй цін, дат, умов.\n"
        "- Якщо фактів для відповіді нема: handoff = true, а в reply скажи, "
        "що передаєш питання команді.\n"
        "- Якщо доречно, м'яко запропонуй записатися."
    ),
    "en": (
        "Rules:\n"
        "- Answer in English, briefly (2-4 sentences), warm and to the point.\n"
        "- Use ONLY the facts above. Never invent prices, dates or policies.\n"
        "- If the facts do not answer the question: handoff = true, and say in reply "
        "that you are passing it to the team.\n"
        "- When it fits, gently offer to book a visit."
    ),
}


def build_system_prompt(profile: Profile) -> str:
    services = "\n".join(
        f"- {s.label}: {s.price}" + (f" ({s.note})" if s.note else "") for s in profile.services
    )
    faq = "\n".join(f"Q: {item.q}\nA: {item.a}" for item in profile.faq)
    trial = profile.booking.trial if profile.booking else None
    facts = [
        f'Business: "{profile.name}" — {profile.tagline}',
        f"About: {profile.about}",
        f"Contact: {profile.owner_contact}",
    ]
    if services:
        facts.append(f"Services and prices:\n{services}")
    if trial and trial.enabled:
        facts.append(f"Trial: {trial.label}")
    if faq:
        facts.append(f"FAQ:\n{faq}")
    return "\n\n".join(facts) + "\n\n" + _RULES[profile.language]


class GeminiAnswerer:
    def __init__(self, api_key: str, models: list[str] | None = None, client=None):
        self.models = models or MODELS
        self._client = client or genai.Client(api_key=api_key)

    async def answer(self, profile: Profile, question: str) -> Answer:
        config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(profile),
            response_mime_type="application/json",
            response_schema=Answer,
        )
        last_error: Exception | None = None
        for model in self.models:
            for attempt in range(RETRIES_PER_MODEL):
                try:
                    result = await self._client.aio.models.generate_content(
                        model=model, contents=question, config=config
                    )
                    return Answer.model_validate_json(result.text or "")
                except ValidationError as e:
                    # The model ignored the schema: treat it like a bad model answer.
                    log.warning("LLM returned non-schema output (model=%s)", model)
                    last_error = e
                    break
                except errors.APIError as e:
                    last_error = e
                    if e.code == RATE_LIMITED:
                        log.warning("LLM rate-limited (model=%s), next model", model)
                        break
                    if e.code != OVERLOADED:
                        raise
                    log.warning("LLM overloaded (model=%s attempt=%d)", model, attempt + 1)
                    await asyncio.sleep(BACKOFF_SECONDS * (attempt + 1))
        raise RuntimeError("all models failed") from last_error
