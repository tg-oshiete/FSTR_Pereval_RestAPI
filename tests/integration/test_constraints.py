"""Direct SQL proves PostgreSQL enforcement independently of API validation."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy.orm import Session

from app.models import Image, PerevalAdded, PerevalImages, User
from tests.test_regressions import photo


pytestmark = pytest.mark.integration


@pytest.fixture
def persisted(pg_client, test_pereval_data):
    test_pereval_data["images"] = [photo()]
    return pg_client.post("/submitData/", json=test_pereval_data).json()["id"]


@pytest.mark.parametrize("table,field,value", [
    ("users", "email", None), ("users", "email", "x" * 255),
    ("users", "name", None), ("users", "fam", ""), ("users", "name", "x" * 101),
    ("users", "phone", "x" * 33), ("users", "otc", "x" * 101),
    ("coords", "latitude", None), ("coords", "latitude", 90.01),
    ("coords", "latitude", -90.01), ("coords", "latitude", float("nan")),
    ("coords", "longitude", 180.01), ("coords", "longitude", -180.01),
    ("coords", "longitude", float("inf")),
    ("coords", "height", None), ("coords", "height", 0), ("coords", "height", 10001),
    ("pereval_added", "title", None), ("pereval_added", "title", "   "),
    ("pereval_added", "title", "x" * 256), ("pereval_added", "beauty_title", "x" * 256),
    ("pereval_added", "other_titles", "x" * 1025), ("pereval_added", "connect", "x" * 2001),
    ("pereval_added", "date_added", None), ("pereval_added", "add_time", None),
    ("pereval_added", "user_id", None), ("pereval_added", "coord_id", None),
    ("pereval_added", "user_id", 2147483647), ("pereval_added", "coord_id", 2147483647),
    ("pereval_added", "status", "invalid"), ("pereval_added", "status", None),
    *(("pereval_added", f"level_{season}", "4A") for season in ("spring", "summer", "winter", "autumn")),
    ("pereval_added", "level_spring", "н/к"),
    ("images", "title", None), ("images", "title", "x" * 256),
    ("images", "date_added", None), ("images", "img", b""), ("images", "img", None),
    ("pereval_images", "id_pereval", None), ("pereval_images", "id_image", None),
    ("pereval_images", "id_pereval", 2147483647), ("pereval_images", "id_image", 2147483647),
    ("pereval_images", "position", None), ("pereval_images", "position", -1),
])
def test_database_rejects_invalid_values(pg_engine, persisted, table, field, value):
    # Identifiers are test constants; values are always bound parameters.
    with pytest.raises((IntegrityError, DataError)):
        with pg_engine.begin() as connection:
            connection.execute(text(f"UPDATE {table} SET {field}=:value"), {"value": value})
    with Session(pg_engine) as fresh:
        assert fresh.get(PerevalAdded, persisted).title == "Тестовый перевал"


def test_uniqueness_and_ownership(pg_client, pg_engine, test_pereval_data, persisted):
    second = pg_client.post("/submitData/", json=test_pereval_data).json()["id"]
    with Session(pg_engine) as fresh:
        first = fresh.get(PerevalAdded, persisted)
        user_id, coord_id = first.user_id, first.coord_id
        image_id = fresh.query(PerevalImages).filter_by(id_pereval=persisted).one().id_image
    statements = [
        ("INSERT INTO users (email,fam,name) SELECT email,fam,name FROM users WHERE id=:id", {"id": user_id}),
        ("UPDATE pereval_added SET coord_id=:coord WHERE id=:id", {"coord": coord_id, "id": second}),
        ("INSERT INTO pereval_images (id_pereval,id_image,position) VALUES (:id,:image,1)", {"id": persisted, "image": image_id}),
        ("INSERT INTO pereval_images (id_pereval,id_image,position) VALUES (:id,:image,1)", {"id": second, "image": image_id}),
        ("DELETE FROM users WHERE id=:id", {"id": user_id}),
        ("DELETE FROM coords WHERE id=:id", {"id": coord_id}),
        # Reuse position with a different photo; image uniqueness cannot mask it.
        ("UPDATE pereval_images SET id_pereval=:id WHERE id_pereval=:second", {"id": persisted, "second": second}),
    ]
    for statement, params in statements:
        with pytest.raises(IntegrityError):
            with pg_engine.begin() as connection:
                connection.execute(text(statement), params)
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM images WHERE id=:id"), {"id": image_id})
    with Session(pg_engine) as fresh:
        assert fresh.query(PerevalImages).filter_by(id_image=image_id).count() == 0
        assert fresh.query(User).count() == 1 and fresh.query(PerevalAdded).count() == 2


def test_postgres_types_and_filter_indexes(pg_engine):
    with pg_engine.connect() as connection:
        assert connection.get_isolation_level() == "READ COMMITTED"
    inspector = inspect(pg_engine)
    coords = {column["name"]: column for column in inspector.get_columns("coords")}
    assert str(coords["height"]["type"]) == "INTEGER"
    for table, fields in {"pereval_added": ("date_added", "add_time"), "images": ("date_added",)}.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert all(columns[field]["type"].timezone for field in fields)
    indexes = inspector.get_indexes("pereval_added")
    assert any(index["column_names"] == ["user_id", "id"] for index in indexes)
    assert any(constraint["column_names"] == ["id_pereval", "position"]
               for constraint in inspector.get_unique_constraints("pereval_images"))
