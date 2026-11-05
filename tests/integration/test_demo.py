from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import demo
from app.models import AccessToken, Coords, DecisionHistory, Image, PerevalAdded, PerevalImages, User
from app.schemas import PerevalUpdate
from app.security import Principal, Role
from app.services import update_pereval
from tests.integration.test_http_blackbox import live_api  # noqa: F401


pytestmark = pytest.mark.integration


def counts(engine):
    with Session(engine) as db:
        return [db.query(model).count() for model in
                (User, PerevalAdded, Coords, Image, PerevalImages, DecisionHistory, AccessToken)]


def test_seed_concurrent_repeat_preserves_rows_and_issues_no_tokens(pg_engine):
    barrier = Barrier(2)

    def invoke():
        barrier.wait(timeout=10)
        return demo.seed(pg_engine)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        first, second = [future.result(timeout=20) for future in futures]
    assert first == second
    assert [row["status"] for row in first] == ["new", "pending", "accepted", "rejected"]
    assert counts(pg_engine) == [4, 4, 4, 4, 4, 5, 0]
    with Session(pg_engine) as db:
        owner = db.scalar(select(User).where(User.email == demo.PROFILES["seed"]["email"]))
        principal = Principal(owner.id, owner.email, Role.AUTHOR)
    with Session(pg_engine) as db:
        update_pereval(db, first[0]["id"], PerevalUpdate(title="Изменено вручную"), principal)
    assert demo.seed(pg_engine) == first
    with Session(pg_engine) as db:
        assert db.get(PerevalAdded, first[0]["id"]).title == "Изменено вручную"
    assert counts(pg_engine) == [4, 4, 4, 4, 4, 5, 0]


def test_seed_failure_rolls_back_entire_collection(pg_engine):
    def fail(*args):
        raise RuntimeError("Injected history failure")

    event.listen(DecisionHistory, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            demo.seed(pg_engine)
    finally:
        event.remove(DecisionHistory, "after_insert", fail)
    assert counts(pg_engine) == [0] * 7
    assert len(demo.seed(pg_engine)) == 4


def test_demo_network_replay_and_token_cleanup(pg_engine, request, capsys):
    client, _, _ = request.getfixturevalue("live_api")
    demo.run(pg_engine, str(client.base_url))
    demo.run(pg_engine, str(client.base_url))
    output = capsys.readouterr().out
    assert output.count("DEMO OK") == 2
    with Session(pg_engine) as db:
        assert db.query(PerevalAdded).count() == 6
        tokens = list(db.scalars(select(AccessToken).join(User).where(
            User.email.in_([profile["email"] for profile in demo.PROFILES.values()]),
        )))
        assert len(tokens) == 6 and all(row.revoked_at is not None for row in tokens)


def test_demo_failure_revokes_already_issued_tokens(pg_engine, monkeypatch):
    original = demo.issue_token
    issued = []

    def fail_second(*args, **kwargs):
        if issued:
            raise RuntimeError("Injected issuance failure")
        result = original(*args, **kwargs)
        issued.append(result)
        return result

    monkeypatch.setattr(demo, "issue_token", fail_second)
    with pytest.raises(RuntimeError, match="Injected"):
        demo.run(pg_engine, "http://127.0.0.1:8000")
    with Session(pg_engine) as db:
        assert db.get(AccessToken, issued[0]["token_id"]).revoked_at is not None
