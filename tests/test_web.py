"""HTTP layer: kit.web.create_app, exercised through FastAPI's TestClient.

The lifespan runs for real (the PTB Application is initialised once), and
updates go HTTP -> secret check -> dedup -> process_update -> handler -> the
fake Bot API from conftest.
"""

import logging
import time

import pytest
from fastapi.testclient import TestClient

from kit.app import Deps
from kit.web import SECRET_HEADER, WEBHOOK_PATH, SeenUpdates, create_app
from tests.conftest import ADMIN_ID, CLIENT_ID, MONDAY_NOON, TOKEN, FakeTelegramRequest

SECRET = "test-webhook-secret"


def start_update(update_id: int = 1, user_id: int = CLIENT_ID, text: str = "/start") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(time.time()),
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Olena"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
        },
    }


@pytest.fixture
def server(dental, store, answerer):
    """make(secret=...) -> (client, fake Bot API); lifespans closed at teardown."""
    opened = []

    def make(secret: str = SECRET):
        request = FakeTelegramRequest()
        deps = Deps(
            profile=dental, store=store, answerer=answerer, admin_id=ADMIN_ID,
            clock=lambda: MONDAY_NOON,
        )  # fmt: skip
        api = create_app(deps, TOKEN, secret, request, FakeTelegramRequest())
        client = TestClient(api)
        client.__enter__()
        opened.append(client)
        return client, request

    yield make
    for client in opened:
        client.__exit__(None, None, None)


def sent(request: FakeTelegramRequest) -> list[dict]:
    return [p for e, p in request.calls if e == "sendMessage"]


@pytest.mark.parametrize("headers", [{}, {SECRET_HEADER: "guess"}, {SECRET_HEADER: ""}])
def test_wrong_or_missing_secret_is_rejected_before_any_work(server, headers):
    client, request = server()
    response = client.post(WEBHOOK_PATH, json=start_update(), headers=headers)
    assert response.status_code == 403
    assert sent(request) == []


def test_unset_secret_fails_closed(server):
    client, request = server(secret="")
    response = client.post(WEBHOOK_PATH, json=start_update(), headers={SECRET_HEADER: ""})
    assert response.status_code == 403
    assert sent(request) == []


def test_update_travels_to_the_handler_and_back(server):
    client, request = server()
    response = client.post(WEBHOOK_PATH, json=start_update(), headers={SECRET_HEADER: SECRET})
    assert response.status_code == 200 and response.json() == {"ok": True}
    [reply] = sent(request)
    assert reply["chat_id"] == CLIENT_ID and reply["text"].startswith("Hi, this is Bright Smile")


def test_a_redelivered_update_is_handled_once(server):
    client, request = server()
    for _ in range(3):
        client.post(WEBHOOK_PATH, json=start_update(update_id=7), headers={SECRET_HEADER: SECRET})
    assert len(sent(request)) == 1


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2, 3]", b'{"no_update_id": true}'])
def test_body_that_is_not_an_update_is_a_400(server, body):
    client, request = server()
    response = client.post(
        WEBHOOK_PATH,
        content=body,
        headers={SECRET_HEADER: SECRET, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert sent(request) == []


def test_a_crashing_handler_still_answers_200(server, store, caplog):
    async def broken(*args, **kwargs):
        raise ConnectionError("database is down")

    store.recent_leads = broken
    client, _ = server()
    with caplog.at_level(logging.ERROR):
        response = client.post(
            WEBHOOK_PATH,
            json=start_update(user_id=ADMIN_ID, text="/leads"),
            headers={SECRET_HEADER: SECRET},
        )
    assert response.status_code == 200
    assert "handler failed" in caplog.text


def test_health_names_the_profile_and_nothing_secret(server):
    client, _ = server()
    response = client.get("/health")
    assert response.json() == {"ok": True, "profile": "bright-smile-demo"}
    assert TOKEN not in response.text and SECRET not in response.text


def test_no_generated_docs_are_exposed(server):
    client, _ = server()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_seen_updates_forgets_the_oldest_beyond_its_limit():
    seen = SeenUpdates(limit=2)
    assert [seen.first_time(i) for i in (1, 2, 1, 3)] == [True, True, False, True]
    assert seen.first_time(1) is True  # 1 was evicted when 3 arrived
