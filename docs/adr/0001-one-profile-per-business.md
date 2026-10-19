# 1. One JSON profile per business

Status: accepted

## Context

The same bot is sold to many small businesses. What differs between them is content — services, prices, opening times, FAQ, the language of the buttons — not behaviour. Earlier bots kept this content in code or in a loosely checked JSON file, so a typo surfaced as a crash in front of a client.

## Decision

Everything client-specific lives in one JSON profile, validated by a strict pydantic model before the bot starts (`python -m kit validate`). Unknown fields, duplicate service codes, times that are not `HH:MM` or out of order, unknown timezones, an enabled module without data and a typo in a `{placeholder}` of an overridden text are all errors with the field's path. Modules (`booking`, `faq`, `ai_answers`) are switched on and off in the profile; the menu, the commands and the handlers follow.

## Consequences

- A new business is a new file, not a fork. Two demo presets show the range: an English dental clinic and a Ukrainian language school.
- Validation reports field errors before cross-field ones, so a badly broken file can take two runs to fix.
- Anything that is not content (a new kind of screen, a payment step) is code, on purpose.
