"""HTTP access matrix and both moderation branches on migrated PostgreSQL."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.cli import revoke_token
from app.models import AccessToken, DecisionHistory, PerevalAdded, User
from tests.test_regressions import create, error


pytestmark = pytest.mark.integration


def decision(client, item_id, actor, expected, status, **extra):
    return client.patch(f"/moderation/perevals/{item_id}/status", headers=actor["headers"],
                        json={"expected_status": expected, "status": status, **extra})


@pytest.mark.parametrize("final", ["accepted", "rejected"])
def test_complete_workflow_and_private_history(pg_client, pg_engine, actors, test_pereval_data, final):
    item_id = create(pg_client, test_pereval_data)
    assert pg_client.get(f"/submitData/{item_id}/history").json() == []
    assert pg_client.patch(f"/submitData/{item_id}", json={"title": "Edited"}).status_code == 200
    queue = pg_client.get("/moderation/perevals", headers=actors["m"]["headers"])
    assert queue.status_code == 200 and [row["id"] for row in queue.json()] == [item_id]
    assert "images" not in queue.json()[0]
    assert decision(pg_client, item_id, actors["m"], "new", "pending").json() == {"id": item_id, "status": "pending"}
    error(pg_client.patch(f"/submitData/{item_id}", json={"title": "Too late"}), 409, "PEREVAL_NOT_EDITABLE")
    assert pg_client.get("/moderation/perevals", headers=actors["m"]["headers"]).json() == []
    assert len(pg_client.get("/moderation/perevals?status=pending", headers=actors["m"]["headers"]).json()) == 1
    reason = {"reason": "  Недостаточно данных  "} if final == "rejected" else {}
    response = decision(pg_client, item_id, actors["m"], "pending", final, **reason)
    assert response.status_code == 200 and response.json() == {"id": item_id, "status": final}
    history = pg_client.get(f"/submitData/{item_id}/history").json()
    assert [(row["old_status"], row["new_status"]) for row in history] == [("new", "pending"), ("pending", final)]
    for row in history:
        assert set(row) == {"id", "pereval_id", "old_status", "new_status", "actor", "changed_at", "reason"}
        assert row["actor"] == {"user_id": actors["m"]["user_id"], "role": "moderator"}
        assert row["pereval_id"] == item_id and row["changed_at"].endswith("Z")
    assert history[-1]["reason"] == ("Недостаточно данных" if final == "rejected" else None)
    assert pg_client.get(f"/submitData/{item_id}/history", headers=actors["m"]["headers"]).json() == history
    assert pg_client.get(f"/submitData/{item_id}/history?limit=1&offset=1").json() == history[1:]
    with Session(pg_engine) as fresh:
        row = fresh.get(PerevalAdded, item_id)
        assert row.title == "Edited" and row.status == final
        assert fresh.query(DecisionHistory).count() == 2


def test_access_matrix_and_owner_checked_before_state(pg_client, actors, test_pereval_data):
    item_id = create(pg_client, test_pereval_data)
    for expected, target in [(None, "new"), ("new", "pending"), ("pending", "accepted")]:
        if expected:
            assert decision(pg_client, item_id, actors["m"], expected, target).status_code == 200
        for path in [f"/submitData/{item_id}", f"/submitData/{item_id}/history",
                     "/submitData/?user__email=string@example.com", "/submitData/?user__email=unknown@example.com"]:
            error(pg_client.get(path, headers=actors["b"]["headers"]), 403, "FORBIDDEN")
        error(pg_client.patch(f"/submitData/{item_id}", json={"title": "Hacked"},
                              headers=actors["b"]["headers"]), 403, "FORBIDDEN")
        error(pg_client.patch(f"/submitData/{item_id}", json={"title": "Moderator edit"},
                              headers=actors["m"]["headers"]), 403, "FORBIDDEN")
    error(pg_client.post("/submitData/", json=test_pereval_data, headers=actors["b"]["headers"]), 403, "FORBIDDEN")
    error(pg_client.post("/submitData/", json=test_pereval_data, headers=actors["m"]["headers"]), 403, "FORBIDDEN")
    for actor in [actors["a"], actors["b"]]:
        error(pg_client.get("/moderation/perevals", headers=actor["headers"]), 403, "FORBIDDEN")
        error(decision(pg_client, item_id, actor, "accepted", "pending"), 403, "FORBIDDEN")
        error(decision(pg_client, 2147483647, actor, "new", "pending"), 403, "FORBIDDEN")
    assert pg_client.get(f"/submitData/{item_id}", headers=actors["m"]["headers"]).status_code == 200
    assert pg_client.get("/submitData/?user__email=unknown@example.com", headers=actors["m"]["headers"]).json() == []
    assert pg_client.get("/submitData/?user__email=other@example.com", headers=actors["b"]["headers"]).json() == []
    for path in ["/submitData/2147483647", "/submitData/2147483647/history"]:
        error(pg_client.get(path), 404, "PEREVAL_NOT_FOUND")
    error(decision(pg_client, 2147483647, actors["m"], "new", "pending"), 404, "PEREVAL_NOT_FOUND")


def test_second_author_owns_only_their_submission(pg_client, pg_engine, actors, test_pereval_data):
    body = {**test_pereval_data, "user": {"email": "other@example.com", "fam": "Other", "name": "Author"}}
    response = pg_client.post("/submitData/", json=body, headers=actors["b"]["headers"])
    assert response.status_code == 201
    item_id = response.json()["id"]
    error(pg_client.get(f"/submitData/{item_id}"), 403, "FORBIDDEN")
    assert pg_client.get(f"/submitData/{item_id}", headers=actors["b"]["headers"]).status_code == 200
    assert pg_client.patch(f"/submitData/{item_id}", headers=actors["b"]["headers"], json={"title": "B's edit"}).status_code == 200
    assert pg_client.get(f"/submitData/{item_id}/history", headers=actors["b"]["headers"]).json() == []
    assert [row["id"] for row in pg_client.get("/submitData/?user__email=other@example.com", headers=actors["b"]["headers"]).json()] == [item_id]
    error(pg_client.get("/moderation/perevals", headers={"X-Role": "moderator", "X-User-ID": str(actors["m"]["user_id"])}), 403, "FORBIDDEN")
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).user_id == actors["b"]["user_id"]


@pytest.mark.parametrize("tamper", ["id", "user_id", "role", "top_user_id", "top_role", "status"])
def test_body_cannot_choose_identity_or_role(pg_client, pg_engine, actors, test_pereval_data, tamper):
    body = deepcopy(test_pereval_data)
    if tamper.startswith("top_"):
        body[tamper[4:]] = actors["m"]["user_id"] if tamper == "top_user_id" else "moderator"
    elif tamper == "status":
        body["status"] = "accepted"
    else:
        body["user"][tamper] = "moderator" if tamper == "role" else actors["m"]["user_id"]
    error(pg_client.post("/submitData/", json=body), 422, "VALIDATION_ERROR")
    with Session(pg_engine) as fresh:
        assert fresh.query(PerevalAdded).count() == 0 and fresh.query(DecisionHistory).count() == 0
        assert fresh.get(User, actors["a"]["user_id"]).email == test_pereval_data["user"]["email"]


@pytest.mark.parametrize("kind", ["missing", "basic", "malformed", "unknown", "expired", "revoked"])
def test_all_private_routes_require_live_token(pg_client, pg_engine, pg_identity, test_pereval_data, kind):
    item_id = create(pg_client, test_pereval_data)
    token = pg_identity["token"]
    if kind == "expired":
        with Session(pg_engine) as session:
            row = session.get(AccessToken, pg_identity["token_id"])
            row.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
            row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            session.commit()
    elif kind == "revoked":
        with Session(pg_engine) as session:
            assert revoke_token(session, pg_identity["token_id"])
    auth = {"missing": "", "basic": "Basic abc", "malformed": "Bearer wrong",
            "unknown": "Bearer " + secrets.token_urlsafe(32)}.get(kind, "Bearer " + token)
    routes = [
        ("POST", "/submitData/", test_pereval_data),
        ("GET", f"/submitData/{item_id}", None),
        ("PATCH", f"/submitData/{item_id}", {"title": "Unauthorized"}),
        ("GET", "/submitData/?user__email=string@example.com", None),
        ("GET", f"/submitData/{item_id}/history", None),
        ("GET", "/moderation/perevals", None),
        ("PATCH", f"/moderation/perevals/{item_id}/status", {"expected_status": "new", "status": "pending"}),
    ]
    for method, path, body in routes:
        response = pg_client.request(method, path, json=body, headers={"Authorization": auth})
        error(response, 401, "UNAUTHORIZED")
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert token not in response.text and "string@example.com" not in response.text
    assert pg_client.get("/health", headers={"Authorization": auth}).status_code == 200
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).status == "new"
        assert fresh.query(DecisionHistory).count() == 0


@pytest.mark.parametrize("reason", [None, "", "  \t\n ", "x" * 1001, 123])
def test_rejection_requires_valid_reason(pg_client, actors, test_pereval_data, reason):
    item_id = create(pg_client, test_pereval_data)
    assert decision(pg_client, item_id, actors["m"], "new", "pending").status_code == 200
    error(decision(pg_client, item_id, actors["m"], "pending", "rejected", reason=reason), 422, "VALIDATION_ERROR")
    error(decision(pg_client, item_id, actors["m"], "pending", "rejected"), 422, "VALIDATION_ERROR")
    assert len(pg_client.get(f"/submitData/{item_id}/history").json()) == 1
    assert pg_client.get(f"/submitData/{item_id}").json()["status"] == "pending"


@pytest.mark.parametrize("final", ["accepted", "rejected"])
def test_invalid_repeated_and_stale_decisions_leave_no_false_history(pg_client, actors, test_pereval_data, final):
    item_id = create(pg_client, test_pereval_data)
    for target in ["new", "accepted", "rejected"]:
        error(decision(pg_client, item_id, actors["m"], "new", target, reason="Test"),
              409, "INVALID_STATUS_TRANSITION")
    error(decision(pg_client, item_id, actors["m"], "pending", "accepted"), 409, "STATUS_CONFLICT")
    assert pg_client.get(f"/submitData/{item_id}/history").json() == []
    for old, new in [("new", "pending"), ("pending", final)]:
        assert decision(pg_client, item_id, actors["m"], old, new, reason="Review").status_code == 200
        error(decision(pg_client, item_id, actors["m"], old, new, reason="Review"), 409, "STATUS_CONFLICT")
    for target in ["new", "pending", "accepted", "rejected"]:
        error(decision(pg_client, item_id, actors["m"], final, target, reason="Test"),
              409, "INVALID_STATUS_TRANSITION")
    assert len(pg_client.get(f"/submitData/{item_id}/history").json()) == 2


@pytest.mark.parametrize("stage", ["status_update", "history_insert", "commit"])
def test_status_and_history_roll_back_together(pg_client, pg_engine, actors, test_pereval_data, stage):
    item_id = create(pg_client, test_pereval_data)

    def fail(*args):
        raise RuntimeError("Injected decision failure")

    target, hook = {"status_update": (PerevalAdded, "after_update"),
                    "history_insert": (DecisionHistory, "after_insert"),
                    "commit": (Session, "before_commit")}[stage]
    event.listen(target, hook, fail)
    try:
        error(decision(pg_client, item_id, actors["m"], "new", "pending"), 500, "INTERNAL_ERROR")
    finally:
        event.remove(target, hook, fail)
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).status == "new"
        assert fresh.query(DecisionHistory).count() == 0
    assert decision(pg_client, item_id, actors["m"], "new", "pending").status_code == 200
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).status == "pending"
        assert fresh.query(DecisionHistory).one().actor_user_id == actors["m"]["user_id"]


def test_queue_pages_filters_and_query_validation(pg_client, actors, test_pereval_data):
    ids = [create(pg_client, test_pereval_data) for _ in range(3)]
    headers = actors["m"]["headers"]
    assert [row["id"] for row in pg_client.get("/moderation/perevals?limit=1&offset=1", headers=headers).json()] == ids[1:2]
    assert decision(pg_client, ids[0], actors["m"], "new", "pending").status_code == 200
    assert [row["id"] for row in pg_client.get("/moderation/perevals", headers=headers).json()] == ids[1:]
    assert pg_client.get("/moderation/perevals?status=rejected", headers=headers).json() == []
    for path in ["/moderation/perevals", f"/submitData/{ids[0]}/history"]:
        for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 2147483648}]:
            error(pg_client.get(path, params=params, headers=headers), 422, "VALIDATION_ERROR")
    error(pg_client.get("/moderation/perevals?status=invalid", headers=headers), 422, "VALIDATION_ERROR")
    for extra in [{"role": "moderator"}, {"actor_user_id": actors["b"]["user_id"]}, {"expected_status": "bad"}]:
        error(pg_client.patch(f"/moderation/perevals/{ids[1]}/status", headers=headers,
                              json={"expected_status": "new", "status": "pending", **extra}), 422, "VALIDATION_ERROR")


def test_openapi_declares_bearer_for_every_private_operation(pg_client):
    document = pg_client.get("/openapi.json").json()
    assert document["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http", "description": "Непрозрачный токен, выданный локальной CLI", "scheme": "bearer",
    }
    for path, routes in document["paths"].items():
        for operation in routes.values():
            if path == "/health":
                assert "security" not in operation
            else:
                assert operation["security"] == [{"HTTPBearer": []}]
                assert {"401", "403", "422", "500"} <= operation["responses"].keys()
