"""HTTP layer: kit.web.create_app, exercised through FastAPI's TestClient.

TestClient runs the app on its own event loop in a thread, so these tests use
the in-memory store; the Postgres store is covered by test_storage.py.

The lifespan runs for real (the PTB Application is initialised once), and
updates go HTTP -> secret check -> dedup -> process_update -> handler -> the
fake Bot API from conftest.
"""

import logging
import time

import pytest
from fastapi.testclient import TestClient

from kit.app import Deps
from kit.storage import MemoryLeadStore, MemoryUpdateLog
from kit.web import SECRET_HEADER, WEBHOOK_PATH, create_app
from tests.conftest import ADMIN_ID, CLIENT_ID, MONDAY_NOON, TOKEN, FakeTelegramRequest, new_lead

SECRET = "test-webhook-secret"
API_TOKEN = "test-api-token"


@pytest.fixture
def store():
    return MemoryLeadStore()


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

    def make(secret: str = SECRET, api_token: str = API_TOKEN):
        request = FakeTelegramRequest()
        deps = Deps(
            profile=dental, store=store, answerer=answerer, admin_id=ADMIN_ID,
            clock=lambda: MONDAY_NOON,
        )  # fmt: skip
        api = create_app(
            deps, TOKEN, secret, request, FakeTelegramRequest(), api_token=api_token
        )
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


def test_docs_describe_the_api_but_not_the_webhook(server):
    client, _ = server()
    assert client.get("/docs").status_code == 404
    assert client.get("/api/docs").status_code == 200
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/leads" in paths and WEBHOOK_PATH not in paths


async def test_memory_update_log_forgets_the_oldest_beyond_its_limit():
    log = MemoryUpdateLog(limit=2)
    assert [await log.first_time("b", i) for i in (1, 2, 1, 3)] == [True, True, False, True]
    assert await log.first_time("b", 1) is True  # 1 was evicted when 3 arrived


# --- /api/leads ----------------------------------------------------------------


def _auth(token: str = API_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("headers", [{}, _auth("guess"), {"Authorization": "Basic a2l0OmtpdA=="}])
def test_api_without_the_right_token_is_401(server, headers):
    client, _ = server()
    response = client.get("/api/leads", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_api_is_switched_off_without_a_configured_token(server):
    client, _ = server(api_token="")
    assert client.get("/api/leads", headers=_auth("")).status_code == 404


async def test_api_pages_through_the_leads_of_this_business_only(server, store, dental, school):
    for slot in ("10:00", "11:00", "12:00"):
        await store.save_lead(new_lead(dental, slot_time=slot), 1, 100, MONDAY_NOON.date())
    await store.save_lead(new_lead(school), 1, 100, MONDAY_NOON.date())
    client, _ = server()

    first = client.get("/api/leads?limit=2", headers=_auth()).json()
    assert [lead["slot_time"] for lead in first["leads"]] == ["12:00", "11:00"]
    after = first["next_before_id"]
    rest = client.get(f"/api/leads?limit=2&before_id={after}", headers=_auth()).json()
    assert [lead["slot_time"] for lead in rest["leads"]] == ["10:00"]
    assert rest["next_before_id"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=500", "before_id=0", "limit=x"])
def test_api_rejects_bad_paging(server, query):
    client, _ = server()
    assert client.get(f"/api/leads?{query}", headers=_auth()).status_code == 422
