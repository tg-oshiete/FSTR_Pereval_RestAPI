"""A separate Uvicorn process, TCP HTTP and migrated PostgreSQL; no app overrides.

The only privileged setup is the documented token CLI. SQL is used solely for
fault injection and to prove concurrent requests are actually waiting in PG.
All contract assertions observe HTTP responses from fresh network requests.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx2 as httpx
import pytest
from sqlalchemy import text

from tests.test_regressions import photo


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def live_api(pg_engine, tmp_path):
    url = pg_engine.url
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("FSTR_", "POSTGRES_", "UVICORN_")) and key != "PORT"}
    env.update(FSTR_DB_HOST=url.host, FSTR_DB_PORT=str(url.port), FSTR_DB_NAME=url.database,
               FSTR_DB_LOGIN=url.username, FSTR_DB_PASS=url.password, PORT="8000",
               PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    # The server starts outside the checkout; no user .env can be read.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--no-access-log"], cwd=tmp_path, env=env,
            stdout=log, stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while True:
                    assert process.poll() is None, "Uvicorn exited before becoming healthy"
                    try:
                        response = client.get("/health")
                        assert response.status_code == 200 and response.json() == {"status": "ok"}
                        break
                    except httpx.TransportError:
                        assert time.monotonic() < deadline, "Uvicorn readiness timed out"
                        time.sleep(0.05)

                def issue(email, role):
                    result = subprocess.run(
                        [sys.executable, "-m", "app.cli", "issue", "--email", email,
                         "--role", role, "--fam", "Network", "--name", "Test"],
                        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
                    )
                    assert result.returncode == 0, "Token CLI failed"
                    credential = json.loads(result.stdout)
                    return {"Authorization": "Bearer " + credential["token"]}

                actors = {name: issue(email, role) for name, email, role in [
                    ("a", "net-author@example.com", "author"),
                    ("b", "net-other@example.com", "author"),
                    ("m", "net-mod@example.com", "moderator"),
                    ("m2", "net-mod2@example.com", "moderator"),
                ]}
                body = {"title": "Network pass", "beauty_title": "пер.",
                        "user": {"email": "net-author@example.com", "fam": "Network", "name": "Test"},
                        "coords": {"latitude": -90, "longitude": 180, "height": 1},
                        "level": {"spring": "1A"}, "images": [photo()]}
                yield client, actors, body
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def checked(response, status, code=None):
    # Avoid displaying request credentials on failure.
    assert response.status_code == status, f"HTTP {response.status_code}, expected {status}"
    assert response.headers["content-type"].startswith("application/json")
    value = response.json()
    if code:
        assert set(value) == {"detail"} and value["detail"]["code"] == code
        assert set(value["detail"]) == ({"code", "message", "errors"} if status == 422 else {"code", "message"})
        if status == 422:
            assert value["detail"]["errors"]
            assert all(set(error) == {"loc", "type", "message"} for error in value["detail"]["errors"])
        if status == 401:
            assert response.headers["www-authenticate"] == "Bearer"
    return value


def submit(client, actors, body):
    value = checked(client.post("/submitData/", headers=actors["a"], json=body), 201)
    assert value["status"] == 201 and isinstance(value["id"], int)
    return value["id"]


@pytest.mark.parametrize("final", ["accepted", "rejected"])
def test_network_workflow_roles_partial_nulls_and_terminal_states(live_api, final):
    client, actors, body = live_api
    item_id = submit(client, actors, body)
    path = f"/submitData/{item_id}"
    before = checked(client.get(path, headers=actors["a"]), 200)
    assert before["coords"] == body["coords"] and before["images"] == body["images"]
    checked(client.get(path), 401, "UNAUTHORIZED")
    for actor in ["b", "m"]:
        checked(client.patch(path, headers=actors[actor], json={"title": "Forbidden"}), 403, "FORBIDDEN")
    checked(client.get(path, headers=actors["b"]), 403, "FORBIDDEN")
    checked(client.get(path + "/history", headers=actors["b"]), 403, "FORBIDDEN")
    checked(client.get("/submitData/?user__email=net-author@example.com", headers=actors["b"]), 403, "FORBIDDEN")
    checked(client.get("/moderation/perevals", headers=actors["a"]), 403, "FORBIDDEN")
    conflict = deepcopy(body)
    conflict["user"]["fam"] = "Conflict"
    checked(client.post("/submitData/", headers=actors["a"], json=conflict), 409, "USER_PROFILE_CONFLICT")
    assert checked(client.get(path, headers=actors["a"]), 200) == before
    assert len(checked(client.get("/submitData/?user__email=net-author@example.com", headers=actors["a"]), 200)) == 1

    checked(client.patch(path, headers=actors["a"], json={
        "beauty_title": None, "coords": {"height": 10000},
        "level": {"spring": None, "winter": "2Б"}, "images": [],
    }), 200)
    after = checked(client.get(path, headers=actors["a"]), 200)
    assert after == {**before, "beauty_title": None, "coords": {**before["coords"], "height": 10000},
                     "level": {"spring": None, "summer": None, "autumn": None, "winter": "2B"}, "images": []}
    assert checked(client.get(path + "/history", headers=actors["a"]), 200) == []
    assert [row["id"] for row in checked(client.get("/moderation/perevals", headers=actors["m"]), 200)] == [item_id]
    for old, new in [("new", "pending"), ("pending", final)]:
        result = checked(client.patch(f"/moderation/perevals/{item_id}/status", headers=actors["m"],
                                     json={"expected_status": old, "status": new, "reason": " Review "}), 200)
        assert result == {"id": item_id, "status": new}
    checked(client.patch(path, headers=actors["a"], json={"title": "Too late"}), 409, "PEREVAL_NOT_EDITABLE")
    assert checked(client.get(path, headers=actors["a"]), 200) == {**after, "status": final}
    history = checked(client.get(path + "/history", headers=actors["a"]), 200)
    assert [(row["old_status"], row["new_status"]) for row in history] == [("new", "pending"), ("pending", final)]
    assert all(row["actor"]["role"] == "moderator" and row["changed_at"].endswith("Z") for row in history)
    assert history[-1]["reason"] == "Review"


def test_network_invalid_patch_is_atomic_and_errors_are_safe(live_api):
    client, actors, body = live_api
    item_id = submit(client, actors, body)
    path = f"/submitData/{item_id}"
    before = checked(client.get(path, headers=actors["a"]), 200)
    for invalid in [{}, {"title": None}, {"coords": None}, {"coords": {}}, {"level": {}},
                    {"images": None}, {"images": [photo()] * 6}, {"images": [{"title": "Invalid", "img": "%%%"}]},
                    {"coords": {"latitude": -90.001}}, {"coords": {"longitude": 180.001}},
                    {"coords": {"height": 10001}}, {"coords": {"height": 1.0}},
                    {"coords": {"height": True}}, {"coords": {"height": "1"}},
                    {"level": {"spring": "4A"}}, {"user": body["user"]}, {"coords": {"unknown": 1}}]:
        checked(client.patch(path, headers=actors["a"], json=invalid), 422, "VALIDATION_ERROR")
        assert checked(client.get(path, headers=actors["a"]), 200) == before
    # A valid title cannot leak through when a later nested field is invalid.
    checked(client.patch(path, headers=actors["a"], json={"title": "Partial leak", "images": [{"title": "Bad", "img": "x"}]}),
            422, "VALIDATION_ERROR")
    checked(client.patch(path, headers=actors["a"], content=b'{"title":'), 422, "VALIDATION_ERROR")
    assert checked(client.get(path, headers=actors["a"]), 200) == before
    checked(client.get("/submitData/2147483647", headers=actors["a"]), 404, "PEREVAL_NOT_FOUND")
    checked(client.get("/submitData/2147483648/history", headers=actors["a"]), 422, "VALIDATION_ERROR")
    checked(client.patch("/moderation/perevals/0/status", headers=actors["m"],
                         json={"expected_status": "new", "status": "pending"}), 422, "VALIDATION_ERROR")
    checked(client.get("/unknown"), 404, "ROUTE_NOT_FOUND")
    response = client.delete("/health")
    checked(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("operation", ["post", "patch", "decision"])
def test_network_database_fault_rolls_back_then_recovers(live_api, pg_engine, operation):
    client, actors, body = live_api
    item_id = submit(client, actors, body)
    path = f"/submitData/{item_id}"
    before = checked(client.get(path, headers=actors["a"]), 200)
    # A real database trigger fails after earlier SQL has run, outside Python
    # mocks. The same request must succeed after the fault is removed.
    table = "decision_history" if operation == "decision" else "pereval_images"
    with pg_engine.begin() as connection:
        connection.execute(text("""CREATE FUNCTION qa_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'QA injected database failure'; END $$"""))
        connection.execute(text(f"CREATE TRIGGER qa_failure BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION qa_failure()"))

    def mutate():
        if operation == "post":
            return client.post("/submitData/", headers=actors["a"], json=body)
        if operation == "patch":
            return client.patch(path, headers=actors["a"], json={"title": "Changed", "images": [photo("blue")]})
        return client.patch(f"/moderation/perevals/{item_id}/status", headers=actors["m"],
                            json={"expected_status": "new", "status": "pending"})

    try:
        response = mutate()
        checked(response, 500, "INTERNAL_ERROR")
        assert "QA injected" not in response.text and "INSERT" not in response.text
        assert checked(client.get(path, headers=actors["a"]), 200) == before
        assert checked(client.get(path + "/history", headers=actors["a"]), 200) == []
        assert len(checked(client.get("/submitData/?user__email=net-author@example.com", headers=actors["a"]), 200)) == 1
        with pg_engine.connect() as connection:
            assert [connection.scalar(text(f"SELECT count(*) FROM {name}")) for name in
                    ["pereval_added", "coords", "images", "pereval_images", "decision_history"]] == [1, 1, 1, 1, 0]
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text(f"DROP TRIGGER qa_failure ON {table}"))
            connection.execute(text("DROP FUNCTION qa_failure()"))
    checked(mutate(), 201 if operation == "post" else 200)


@pytest.mark.parametrize("initial", ["new", "pending"])
def test_network_competing_moderators_commit_only_one_decision(live_api, pg_engine, initial):
    client, actors, body = live_api
    item_id = submit(client, actors, body)
    route = f"/moderation/perevals/{item_id}/status"
    if initial == "pending":
        checked(client.patch(route, headers=actors["m"], json={"expected_status": "new", "status": "pending"}), 200)
    statuses = ["pending", "pending"] if initial == "new" else ["accepted", "rejected"]

    def decide(actor, status):
        with httpx.Client(base_url=str(client.base_url), timeout=20, trust_env=False) as worker:
            return worker.patch(route, headers=actors[actor],
                                json={"expected_status": initial, "status": status, "reason": "Concurrent review"})

    # Hold a third connection's row lock until BOTH HTTP requests are blocked.
    # The test cannot accidentally pass because requests happened sequentially.
    with pg_engine.connect() as gate, ThreadPoolExecutor(max_workers=2) as pool:
        transaction = gate.begin()
        gate.execute(text("SELECT id FROM pereval_added WHERE id=:id FOR UPDATE"), {"id": item_id})
        gate_pid = gate.scalar(text("SELECT pg_backend_pid()"))
        first = pool.submit(decide, "m", statuses[0])
        second = pool.submit(decide, "m2", statuses[1])
        try:
            deadline = time.monotonic() + 10
            with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                while observer.scalar(text("""SELECT count(*) FROM pg_stat_activity
                        WHERE datname=current_database() AND wait_event_type='Lock'
                        AND query LIKE '%FROM pereval_added%FOR UPDATE%'
                        AND pid <> :gate"""), {"gate": gate_pid}) < 2:
                    assert time.monotonic() < deadline, "Both HTTP transactions must reach the row lock"
                    time.sleep(0.01)
            assert not first.done() and not second.done()
        finally:
            transaction.rollback()
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    checked(next(response for response in responses if response.status_code == 409), 409, "STATUS_CONFLICT")
    final = checked(client.get(f"/submitData/{item_id}", headers=actors["a"]), 200)
    assert final["status"] == winner.json()["status"]
    history = checked(client.get(f"/submitData/{item_id}/history", headers=actors["a"]), 200)
    assert len(history) == (1 if initial == "new" else 2)
    assert history[-1]["old_status"] == initial and history[-1]["new_status"] == final["status"]
