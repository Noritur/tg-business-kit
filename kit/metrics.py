"""Prometheus metrics, served by `serve` at GET /metrics behind METRICS_TOKEN.

Label values come from fixed sets only: results, outcomes and the model names
of kit.llm. Never a user id, a chat id, a path or message text, so the number
of series stays small and nothing personal reaches Prometheus.

One process, one registry. The webhook server is a single uvicorn process
(the app object goes to uvicorn.run, so there are no workers to fork), which
is why prometheus_client's multiprocess mode is not needed. Long polling
counts as well but has no HTTP server, so its numbers are not exposed.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    Info,
    generate_latest,
)

REGISTRY = CollectorRegistry()

UPDATE_RESULTS = ("processed", "duplicate", "failed", "rejected_secret", "rejected_body")
BOOKING_RESULTS = ("created", "slot_taken", "too_many", "error")

UPDATES = Counter(
    "kit_updates",
    "Webhook deliveries and polled updates by result; a retried delivery counts again",
    ["result"],
    registry=REGISTRY,
)
UPDATE_SECONDS = Histogram(
    "kit_update_duration_seconds",
    "Time to process one update, Bot API and Gemini calls included",
    # An answer from Gemini takes seconds, and a retry after a 503 longer.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)
BOOKINGS = Counter("kit_bookings", "Booking confirmations by result", ["result"], registry=REGISTRY)
LLM_REQUESTS = Counter(
    "kit_llm_requests",
    "Gemini calls by model and outcome",
    ["model", "outcome"],
    registry=REGISTRY,
)
BUILD = Info("kit_build", "Version and profile of the running bot", registry=REGISTRY)

# Every known series exists from the start at 0, so rate() and alerts see the
# first increment instead of a series appearing out of nowhere.
for _result in UPDATE_RESULTS:
    UPDATES.labels(result=_result)
for _result in BOOKING_RESULTS:
    BOOKINGS.labels(result=_result)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
