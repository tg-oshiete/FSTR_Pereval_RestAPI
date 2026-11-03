"""Upgrade real previous schemas, including shared ownership and invalid rows."""

import math

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.database import Base
from tests.integration.conftest import migrate


pytestmark = pytest.mark.integration
TABLES = ("users", "coords", "pereval_added", "images", "pereval_images")


def snapshot(engine):
    def comparable(value):
        if isinstance(value, memoryview):
            return value.tobytes()
        if isinstance(value, float) and math.isnan(value):
            return "NaN"
        return value

    with engine.connect() as connection:
        return {name: [{key: comparable(value) for key, value in row.items()}
                       for row in connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings()]
                for name in TABLES}


def seed_legacy(engine):
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (email,phone,fam,name,otc) VALUES ('legacy@example.com','123','Legacy','Test',NULL)"))
        connection.execute(text("INSERT INTO coords (latitude,longitude,height) VALUES (20.5,80.5,3200),(21,81,3300)"))
        connection.execute(text("""INSERT INTO pereval_added
            (title,beauty_title,other_titles,connect,user_id,coord_id,level_spring,add_time)
            VALUES ('First','пер.','Other','Approach',1,1,'1а','2026-11-03T15:00:00.123456+03:00'),
                   ('Second',NULL,NULL,NULL,1,1,'2Б','2026-11-03T12:00:00Z')"""))
        connection.execute(text("INSERT INTO images (img,title) VALUES (:photo,'Shared'),(:orphan,'Orphan')"),
                           {"photo": b"legacy bytes", "orphan": b"orphan bytes"})
        # Duplicate pairs and shared images/coordinates are all preserved by copying.
        connection.execute(text("INSERT INTO pereval_images (id_pereval,id_image) VALUES (1,1),(1,1),(2,1)"))


def test_empty_upgrade_matches_metadata_and_is_repeatable(pg_database):
    migrate(pg_database)
    migrate(pg_database)
    with pg_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert int(connection.scalar(text("SHOW server_version_num"))) // 10000 == 18
    migrate(pg_database, "0002", downgrade=True)
    migrate(pg_database)
    with pg_database.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


@pytest.mark.parametrize("previous", ("0001", "0002"))
def test_upgrade_preserves_existing_rows_and_splits_shared_ownership(pg_database, previous):
    migrate(pg_database, previous)
    seed_legacy(pg_database)
    before = snapshot(pg_database)
    migrate(pg_database)
    after = snapshot(pg_database)
    assert after["users"] == before["users"]
    assert after["coords"][:2] == before["coords"]  # Includes an unrelated orphan.
    assert len(after["coords"]) == 3
    assert after["images"][:2] == before["images"]  # IDs, timestamps and bytes unchanged.
    assert len(after["images"]) == 4
    for old, new in zip(before["pereval_added"], after["pereval_added"]):
        assert new["id"] == old["id"]
        assert new["level_spring"] == {"1а": "1A", "2Б": "2B"}[old["level_spring"]]
        assert all(new[field] == value for field, value in old.items() if field not in {"level_spring", "coord_id"})
        coords = next(row for row in after["coords"] if row["id"] == new["coord_id"])
        assert {key: value for key, value in coords.items() if key != "id"} == {
            "latitude": 20.5, "longitude": 80.5, "height": 3200,
        }
    assert len({row["coord_id"] for row in after["pereval_added"]}) == 2
    assert [row["id"] for row in after["pereval_images"]] == [row["id"] for row in before["pereval_images"]]
    assert [row["position"] for row in after["pereval_images"]] == [0, 1, 0]
    assert len({row["id_image"] for row in after["pereval_images"]}) == 3
    for link in after["pereval_images"]:
        image = next(row for row in after["images"] if row["id"] == link["id_image"])
        assert image["img"] == b"legacy bytes" and image["title"] == "Shared"
    migrate(pg_database)
    assert snapshot(pg_database) == after
    migrate(pg_database, "0002", downgrade=True)
    migrate(pg_database)
    assert snapshot(pg_database) == after


@pytest.mark.parametrize("statement,diagnostic", [
    ("UPDATE coords SET height=3200.5 WHERE id=1", "coords.height"),
    ("UPDATE coords SET latitude='NaN' WHERE id=1", "coords.latitude"),
    ("UPDATE coords SET longitude=181 WHERE id=1", "coords.longitude"),
    ("UPDATE pereval_added SET title=repeat('x',256) WHERE id=1", "pereval_added.title"),
    ("UPDATE pereval_added SET level_winter='4A' WHERE id=1", "level_winter"),
    ("UPDATE pereval_added SET user_id=NULL WHERE id=1", "user_id"),
    ("UPDATE pereval_added SET add_time=NULL WHERE id=1", "add_time"),
    ("UPDATE pereval_added SET date_added=NULL WHERE id=1", "date_added"),
    ("UPDATE images SET title=NULL WHERE id=1", "images.title"),
    ("UPDATE pereval_images SET id_image=NULL WHERE id=1", "id_image"),
    ("UPDATE users SET fam='   ' WHERE id=1", "users.fam"),
    ("UPDATE users SET email='Legacy@EXAMPLE.COM' WHERE id=1", "users.email"),
])
def test_invalid_legacy_rows_stop_before_mutation(pg_database, statement, diagnostic):
    migrate(pg_database, "0002")
    seed_legacy(pg_database)
    with pg_database.begin() as connection:
        connection.execute(text(statement))
    before = snapshot(pg_database)
    with pytest.raises(RuntimeError, match="0003 preflight failed") as error:
        migrate(pg_database)
    assert diagnostic in str(error.value) and "ids=[1]" in str(error.value)
    assert "legacy@example.com" not in str(error.value)
    assert snapshot(pg_database) == before
    with pg_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
    assert "position" not in {column["name"] for column in inspect(pg_database).get_columns("pereval_images")}
    # Explicit operator correction permits retry without deleting the record.
    if "height=" in statement:
        correction = "UPDATE coords SET height=3200 WHERE id=1"
    elif "latitude=" in statement:
        correction = "UPDATE coords SET latitude=20.5 WHERE id=1"
    elif "longitude=" in statement:
        correction = "UPDATE coords SET longitude=80.5 WHERE id=1"
    else:
        correction = None
    if correction:
        with pg_database.begin() as connection:
            connection.execute(text(correction))
        migrate(pg_database)
        assert len(snapshot(pg_database)["pereval_added"]) == 2


def test_naive_legacy_column_requires_explicit_timezone_decision(pg_database):
    migrate(pg_database, "0002")
    seed_legacy(pg_database)
    with pg_database.begin() as connection:
        connection.execute(text("ALTER TABLE pereval_added ALTER COLUMN add_time TYPE TIMESTAMP WITHOUT TIME ZONE USING add_time AT TIME ZONE 'UTC'"))
    with pytest.raises(RuntimeError, match="explicitly migrate its source timezone"):
        migrate(pg_database)
    with pg_database.begin() as connection:
        # Here the fixture's known source zone is UTC; real operators must choose it.
        connection.execute(text("ALTER TABLE pereval_added ALTER COLUMN add_time TYPE TIMESTAMP WITH TIME ZONE USING add_time AT TIME ZONE 'UTC'"))
    migrate(pg_database)


def test_known_three_character_alias_is_canonicalized(pg_database):
    migrate(pg_database, "0002")
    seed_legacy(pg_database)
    with pg_database.begin() as connection:
        connection.execute(text("UPDATE pereval_added SET level_summer='н/к', level_winter=' 1a' WHERE id=1"))
    migrate(pg_database)
    with pg_database.connect() as connection:
        assert connection.execute(text("SELECT level_summer,level_winter FROM pereval_added WHERE id=1")).one() == ("n/k", "1A")
