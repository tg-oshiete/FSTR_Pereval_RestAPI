from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import threading

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import services
from app.models import Coords, Image, PerevalAdded, PerevalImages, User
from app.schemas import PerevalCreate
from tests.test_regressions import photo


pytestmark = pytest.mark.integration
MODELS = (User, Coords, PerevalAdded, Image, PerevalImages)


@pytest.mark.parametrize("stage", (Coords, PerevalAdded, Image, PerevalImages, "commit"))
def test_create_failure_after_insert_rolls_back_before_session_close(pg_engine, pg_identity, test_pereval_data, stage):
    test_pereval_data["images"] = [photo()]

    def fail(*args):
        if stage == "commit" and args[0].in_nested_transaction():
            return
        raise RuntimeError("Injected after INSERT")

    with Session(pg_engine, autoflush=False) as session:
        target, hook = (Session, "before_commit") if stage == "commit" else (stage, "after_insert")
        event.listen(target, hook, fail)
        try:
            with pytest.raises(RuntimeError, match="Injected"):
                services.create_pereval(session, PerevalCreate.model_validate(test_pereval_data), pg_identity["principal"])
        finally:
            event.remove(target, hook, fail)
        assert not session.in_transaction()  # Rollback happened before Session.close().
        with Session(pg_engine) as fresh:
            assert fresh.query(User).count() == 1
            assert all(fresh.query(model).count() == 0 for model in MODELS if model is not User)
        # The same session remains usable after the rollback.
        assert services.create_pereval(session, PerevalCreate.model_validate(test_pereval_data), pg_identity["principal"]) > 0
        assert not session.in_transaction()


@pytest.mark.parametrize("stage", (Coords, PerevalAdded, Image, PerevalImages))
def test_failure_preserves_existing_user_and_submission(pg_client, pg_engine, test_pereval_data, stage):
    test_pereval_data["user"].update(phone="+7 123", otc="Тестович")
    with Session(pg_engine) as operator:
        user = operator.query(User).one()
        user.phone, user.otc = "+7 123", "Тестович"
        operator.commit()
    test_pereval_data["images"] = [photo()]
    first = pg_client.post("/submitData/", json=test_pereval_data).json()["id"]
    before = pg_client.get(f"/submitData/{first}").json()

    def fail(*args):
        raise RuntimeError("Injected")

    event.listen(stage, "after_insert", fail)
    try:
        response = pg_client.post("/submitData/", json=test_pereval_data)
        assert response.status_code == 500
    finally:
        event.remove(stage, "after_insert", fail)
    assert pg_client.get(f"/submitData/{first}").json() == before
    with Session(pg_engine) as fresh:
        assert all(fresh.query(model).count() == 1 for model in MODELS)
        user = fresh.query(User).one()
        assert (user.phone, user.otc, user.fam, user.name) == ("+7 123", "Тестович", "Иванов", "Иван")


@pytest.mark.parametrize("stage", (Image, PerevalImages, "commit"))
def test_update_failure_restores_fields_and_old_photos(pg_client, pg_engine, test_pereval_data, stage):
    test_pereval_data["images"] = [photo(), photo("green")]
    item_id = pg_client.post("/submitData/", json=test_pereval_data).json()["id"]
    before = pg_client.get(f"/submitData/{item_id}").json()
    with Session(pg_engine) as fresh:
        old_image_ids = list(fresh.scalars(select(Image.id).order_by(Image.id)))

    def fail(*args):
        raise RuntimeError("Injected")

    target, hook = (Session, "before_commit") if stage == "commit" else (stage, "after_insert")
    event.listen(target, hook, fail)
    try:
        response = pg_client.patch(f"/submitData/{item_id}", json={
            "title": "Changed", "coords": {"height": 9999}, "level": {"spring": "3B"},
            "images": [photo("blue")],
        })
        assert response.status_code == 500
    finally:
        event.remove(target, hook, fail)
    assert pg_client.get(f"/submitData/{item_id}").json() == before
    with Session(pg_engine) as fresh:
        assert list(fresh.scalars(select(Image.id).order_by(Image.id))) == old_image_ids
        assert fresh.query(PerevalImages).count() == 2
        row = fresh.get(PerevalAdded, item_id)
        assert row.coords.height == 3200 and row.level_spring is None


@pytest.mark.parametrize("conflict", (False, True))
def test_parallel_posts_use_provisioned_identity(pg_client, pg_engine, test_pereval_data, conflict):
    # Both transactions read the same CLI-provisioned identity before writing.
    barrier = threading.Barrier(2, timeout=10)
    local = threading.local()
    reached = []

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if (statement.startswith("SELECT") and "FROM users" in statement and "access_tokens" not in statement
                and not getattr(local, "seen", False)):
            local.seen = True
            reached.append(threading.get_ident())
            barrier.wait()

    payloads = [deepcopy(test_pereval_data), deepcopy(test_pereval_data)]
    if conflict:
        payloads[1]["user"]["name"] = "Другой"
    event.listen(pg_engine, "after_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(pg_client.post, "/submitData/", json=payload) for payload in payloads]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(pg_engine, "after_cursor_execute", synchronize)
    assert len(reached) == 2
    assert sorted(response.status_code for response in results) == ([201, 409] if conflict else [201, 201])
    if conflict:
        rejected = next(response for response in results if response.status_code == 409)
        assert rejected.json()["detail"]["code"] == "USER_PROFILE_CONFLICT"
    with Session(pg_engine) as fresh:
        assert fresh.query(User).count() == 1
        rows = fresh.query(PerevalAdded).all()
        assert len(rows) == (1 if conflict else 2)
        assert len({row.user_id for row in rows}) == 1
        assert fresh.query(Coords).count() == len(rows)
        winner = next(index for index, response in enumerate(results) if response.status_code == 201)
        assert fresh.query(User).one().name == payloads[winner]["user"]["name"]


def test_integer_height_and_aware_time_round_trip_new_sessions(pg_client, pg_engine, test_pereval_data):
    test_pereval_data.update(add_time="2026-11-03T15:00:00.123456+03:00", images=[photo("blue"), photo()])
    test_pereval_data["coords"] = {"latitude": -90, "longitude": 180.0, "height": 10000}
    item_id = pg_client.post("/submitData/", json=test_pereval_data).json()["id"]
    with Session(pg_engine) as fresh:
        row = fresh.get(PerevalAdded, item_id)
        assert type(row.coords.height) is int
        assert row.add_time == datetime(2026, 11, 3, 12, 0, 0, 123456, tzinfo=timezone.utc)
        assert row.date_added.utcoffset() is not None
    result = pg_client.get(f"/submitData/{item_id}").json()
    assert result["add_time"] == "2026-11-03T12:00:00.123456Z"
    assert result["coords"] == test_pereval_data["coords"]
    assert type(result["coords"]["height"]) is int
    assert result["images"] == test_pereval_data["images"]
    assert pg_client.patch(f"/submitData/{item_id}", json={"coords": {"height": 1}}).status_code == 200
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, item_id).coords.height == 1
