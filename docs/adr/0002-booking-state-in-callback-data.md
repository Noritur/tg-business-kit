# 2. Booking state in callback_data

Status: accepted

## Context

A booking takes four taps: service, day, time, confirm. The bot must work as a long-polling process and as a webhook that may restart between any two taps, so state kept in process memory would be lost.

## Decision

Each button carries the whole step in its `callback_data` (`b:t:<service>:<YYMMDD>:<HHMM>`), within Telegram's 64-byte limit; every builder checks the budget and a test checks the longest button of every preset. Because a button can be days old, from an older version of the bot, or forged by a modified client, every step re-checks it: the day must be in the offered window of the business's timezone, the time in that weekday's template, and the slot free. Malformed data is acknowledged and ignored.

## Consequences

- No session storage and nothing to expire.
- The grammar is the API of the buttons: changing it means old buttons in old chats stop working, which is acceptable because they are re-checked anyway.
- Service codes are limited to six lowercase characters to keep the budget comfortable.
