"""Two independent PostgreSQL transactions, synchronized at the actual row lock."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import services
from app.models import DecisionHistory, PerevalAdded
from app.schemas import PerevalUpdate, StatusUpdate
from app.security import Role
from tests.auth_helpers import provision
from tests.integration.test_access_moderation import decision
from tests.test_regressions import create, photo


pytestmark = pytest.mark.integration


def locked_race(engine, first_action, second_action):
    """Hold transaction 1 after FOR UPDATE, observe 2 blocked, then release 1.

    Events coordinate workers; pg_blocking_pids proves the database contention.
    No sleep or timing assumption determines who acquires the lock first.
    """
    locked, release, second_entering = threading.Event(), threading.Event(), threading.Event()
    pids = {}

    def is_lock(statement):
        return "FROM pereval_added" in statement and "FOR UPDATE" in statement

    def before(connection, cursor, statement, parameters, context, executemany):
        if connection.info.get("race_worker") == "second" and is_lock(statement):
            second_entering.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if connection.info.get("race_worker") == "first" and is_lock(statement):
            locked.set()
            assert release.wait(10), "Coordinator did not release the first transaction"

    def worker(name, action):
        with engine.connect() as connection:
            pids[name] = connection.connection.driver_connection.get_backend_pid()
            connection.info["race_worker"] = name
            try:
                with Session(connection, autoflush=False) as session:
                    try:
                        action(session)
                        result = "ok"
                    except (services.StatusConflict, services.PerevalNotEditable) as exc:
                        result = type(exc).__name__
                    assert not session.in_transaction(), "Service must commit/rollback before returning"
                    return result
            finally:
                connection.info.pop("race_worker", None)

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "first", first_action)
            try:
                assert locked.wait(10), "First worker did not acquire FOR UPDATE"
                second = pool.submit(worker, "second", second_action)
                assert second_entering.wait(10), "Second worker did not attempt FOR UPDATE"
                assert pids["first"] != pids["second"]
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    assert observer.connection.driver_connection.get_backend_pid() not in pids.values()
                    deadline = time.monotonic() + 5
                    while not observer.scalar(text("SELECT :first = ANY(pg_blocking_pids(:second))"), pids):
                        assert time.monotonic() < deadline, "PostgreSQL did not report the competing row lock"
                assert not first.done() and not second.done()
            finally:
                release.set()
            return first.result(timeout=10), second.result(timeout=10)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)


@pytest.mark.parametrize("initial", ["new", "pending"])
def test_only_one_competing_decision_from_expected_status(pg_client, pg_engine, actors, test_pereval_data, initial):
    item_id = create(pg_client, test_pereval_data)
    if initial == "pending":
        assert decision(pg_client, item_id, actors["m"], "new", "pending").status_code == 200
    second_mod = provision(pg_engine, {"email": "mod2@example.com", "fam": "Other", "name": "Moderator"}, Role.MODERATOR)
    first_status, second_status = ("pending", "pending") if initial == "new" else ("accepted", "rejected")

    def first(db):
        services.change_status(db, item_id, StatusUpdate(expected_status=initial, status=first_status), actors["m"]["principal"])

    def second(db):
        services.change_status(db, item_id, StatusUpdate(expected_status=initial, status=second_status, reason="Other decision"),
                               second_mod["principal"])

    assert locked_race(pg_engine, first, second) == ("ok", "StatusConflict")
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).status == first_status
        history = fresh.scalars(select(DecisionHistory).order_by(DecisionHistory.id)).all()
        assert len(history) == (1 if initial == "new" else 2)
        assert history[-1].old_status == initial and history[-1].new_status == first_status
        assert history[-1].actor_user_id == actors["m"]["user_id"]
        assert all(row.actor_user_id != second_mod["user_id"] for row in history)


@pytest.mark.parametrize("first", ["edit", "moderate"])
def test_edit_and_start_of_moderation_share_row_lock(pg_client, pg_engine, actors, test_pereval_data, first):
    test_pereval_data["images"] = [photo()]
    item_id = create(pg_client, test_pereval_data)
    before = pg_client.get(f"/submitData/{item_id}").json()

    def edit(db):
        services.update_pereval(db, item_id, PerevalUpdate.model_validate({
            "title": "Changed before moderation", "coords": {"height": 3300},
            "level": {"winter": "2A"}, "images": [photo("blue")],
        }), actors["a"]["principal"])

    def moderate(db):
        services.change_status(db, item_id, StatusUpdate(expected_status="new", status="pending"), actors["m"]["principal"])

    actions = {"edit": (edit, moderate), "moderate": (moderate, edit)}
    assert locked_race(pg_engine, *actions[first]) == (("ok", "ok") if first == "edit" else ("ok", "PerevalNotEditable"))
    after = pg_client.get(f"/submitData/{item_id}").json()
    assert after["status"] == "pending"
    if first == "edit":
        assert after["title"] == "Changed before moderation" and after["coords"]["height"] == 3300
        assert after["level"]["winter"] == "2A" and after["images"] == [photo("blue")]
    else:
        assert after == {**before, "status": "pending"}
    with Session(pg_engine) as fresh:
        row = fresh.query(DecisionHistory).one()
        assert row.old_status == "new" and row.new_status == "pending"
