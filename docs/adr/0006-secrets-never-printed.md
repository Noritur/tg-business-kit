# 6. Secrets are never printed

Status: accepted

## Context

The bot handles a Telegram token, an AI key, a webhook secret, a database password and an API token. Each has a way to leak that is easy to miss: every Bot API URL contains the token and HTTP client logs print URLs; a pydantic `ValidationError` for settings prints the whole input, so one missing key shows every other secret in the terminal; a token typed as a command argument lands in shell history.

## Decision

- Settings load through `load_settings()`, which names the missing or invalid keys and never their values. Secrets are `SecretStr`.
- `httpx` logging is lowered to WARNING in every entry point; scripts never print a URL or an exception that could carry one.
- Secrets reach `.env` through `scripts/setup_env.py`: hidden prompts, values validated (the token with `getMe`), generated secrets created by the script, the file written with mode 600, statuses printed, values never.
- The webhook secret travels in a header, so access logs hold nothing worth stealing.
- Tests assert that output contains no secret value.

## Consequences

- Debugging shows which key is wrong, never what it contains.
- A person types each secret once, into a hidden prompt.
