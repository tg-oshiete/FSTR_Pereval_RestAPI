"""Observable API regressions, persisted through independent sessions."""

import base64
from io import BytesIO
import logging

from PIL import Image as PILImage
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Coords, Image, PerevalAdded, PerevalImages, User


def photo(color="red", size=(2, 2), format="PNG", **options):
    output = BytesIO()
    with PILImage.new("RGB", size, color) as image:
        image.save(output, format=format, **options)
    return {"title": color, "img": base64.b64encode(output.getvalue()).decode("ascii")}


def create(client, payload):
    response = client.post("/submitData/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def error(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    if status == 422:
        assert detail["errors"]
        for issue in detail["errors"]:
            assert set(issue) == {"loc", "type", "message"}
    else:
        assert set(detail) == {"code", "message"}
    return detail


def test_post_status_matches_http(client, test_pereval_data):
    response = client.post("/submitData/", json=test_pereval_data)
    assert response.status_code == 201
    assert response.json() == {"id": 1, "status": 201, "message": "Отправлено успешно"}


@pytest.mark.parametrize("status", ["accepted", "pending", "rejected"])
def test_non_new_patch_is_conflict_and_does_not_change_data(client, db, test_pereval_data, status):
    pereval_id = create(client, test_pereval_data)
    db.get(PerevalAdded, pereval_id).status = status
    db.commit()
    error(client.patch(f"/submitData/{pereval_id}", json={"title": "Changed"}),
          409, "PEREVAL_NOT_EDITABLE")
    with Session(db.get_bind()) as fresh:
        assert fresh.get(PerevalAdded, pereval_id).title == test_pereval_data["title"]


@pytest.mark.parametrize("method", ["get", "patch"])
def test_missing_id(client, method):
    kwargs = {"json": {"title": "Changed"}} if method == "patch" else {}
    error(getattr(client, method)("/submitData/2147483647", **kwargs), 404, "PEREVAL_NOT_FOUND")


@pytest.mark.parametrize("season", ["spring", "summer", "autumn", "winter"])
def test_each_season_commits_and_get_reads_new_session(client, db, test_app, test_pereval_data, season):
    pereval_id = create(client, test_pereval_data)
    response = client.patch(f"/submitData/{pereval_id}", json={"level": {season: "2A"}})
    assert response.status_code == 200
    assert response.json() == {"state": 1, "message": "Обновлено"}
    with Session(db.get_bind()) as fresh:
        assert getattr(fresh.get(PerevalAdded, pereval_id), f"level_{season}") == "2A"
        test_app.state.test_db = fresh
        try:
            result = client.get(f"/submitData/{pereval_id}").json()
            assert result["level"][season] == "2A"
            assert all(value is None for key, value in result["level"].items() if key != season)
        finally:
            test_app.state.test_db = db


def test_partial_coords_omission_and_nullable_clearing(client, db, test_pereval_data):
    test_pereval_data.update(beauty_title="пер.", other_titles="Old", connect="Approach")
    test_pereval_data["level"] = {"spring": "1A", "winter": "2B"}
    pereval_id = create(client, test_pereval_data)
    response = client.patch(f"/submitData/{pereval_id}", json={
        "coords": {"height": 3300}, "level": {"spring": None}, "other_titles": None,
    })
    assert response.status_code == 200, response.text
    with Session(db.get_bind()) as fresh:
        row = fresh.get(PerevalAdded, pereval_id)
        assert (row.coords.latitude, row.coords.longitude, row.coords.height) == (20, 80, 3300)
        assert row.level_spring is None and row.level_winter == "2B"
        assert row.other_titles is None and row.beauty_title == "пер." and row.connect == "Approach"
    result = client.get(f"/submitData/{pereval_id}").json()
    assert result["other_titles"] is None
    assert result["level"]["winter"] == "2B"


INVALID_PATCHES = [
    {}, {"coords": {}}, {"level": {}}, {"title": ""}, {"title": " \t "},
    {"title": None}, {"coords": None}, {"level": None}, {"images": None}, {"add_time": None},
    {"coords": {"height": None}}, {"coords": {"latitude": 91}},
    {"coords": {"height": 3200.0}}, {"coords": {"height": True}},
    {"coords": {"latitude": "20"}}, {"coords": {"longitude": -181}},
    {"coords": {"height": 10001}}, {"user": {"name": "Hacked"}},
    {"status": "accepted"}, {"level": {"extra": "2A"}},
    {"coords": {"height": 3400, "extra": 1}},
    {"images": [{"title": "Photo", "img": "a", "id": 1}]},
]


@pytest.mark.parametrize("patch", INVALID_PATCHES)
def test_invalid_patch_is_atomic(client, db, test_pereval_data, patch):
    test_pereval_data["images"] = [photo()]
    pereval_id = create(client, test_pereval_data)
    before = client.get(f"/submitData/{pereval_id}").json()
    error(client.patch(f"/submitData/{pereval_id}", json=patch), 422, "VALIDATION_ERROR")
    assert client.get(f"/submitData/{pereval_id}").json() == before
    with Session(db.get_bind()) as fresh:
        assert fresh.query(Image).count() == fresh.query(PerevalImages).count() == 1
        assert fresh.get(PerevalAdded, pereval_id).title == before["title"]


def test_replacing_and_clearing_images_remove_owned_rows(client, db, test_pereval_data):
    test_pereval_data["images"] = [photo()]
    pereval_id = create(client, test_pereval_data)
    replacement = photo("blue")
    assert client.patch(f"/submitData/{pereval_id}", json={"images": [replacement]}).status_code == 200
    with Session(db.get_bind()) as fresh:
        assert fresh.query(Image).count() == fresh.query(PerevalImages).count() == 1
        assert fresh.query(Image).one().img == base64.b64decode(replacement["img"])
    assert client.get(f"/submitData/{pereval_id}").json()["images"] == [replacement]
    assert client.patch(f"/submitData/{pereval_id}", json={"images": []}).status_code == 200
    with Session(db.get_bind()) as fresh:
        assert fresh.query(Image).count() == fresh.query(PerevalImages).count() == 0
    assert client.get(f"/submitData/{pereval_id}").json()["images"] == []


def test_new_shared_photo_is_rejected(client, db, test_pereval_data):
    test_pereval_data["images"] = [photo()]
    first = create(client, test_pereval_data)
    second = create(client, test_pereval_data)
    first_image = db.query(PerevalImages).filter_by(id_pereval=first).one().id_image
    db.add(PerevalImages(id_pereval=second, id_image=first_image, position=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    before = client.get(f"/submitData/{second}").json()["images"]
    assert client.patch(f"/submitData/{first}", json={"images": []}).status_code == 200
    with Session(db.get_bind()) as fresh:
        assert fresh.get(Image, first_image) is None
        assert fresh.query(PerevalImages).filter_by(id_image=first_image).count() == 0
    assert client.get(f"/submitData/{second}").json()["images"] == before


def test_replacement_does_not_collect_unrelated_orphans(client, db, test_pereval_data):
    orphan = Image(title="Legacy orphan", img=base64.b64decode(photo()["img"]))
    db.add(orphan)
    db.commit()
    orphan_id = orphan.id
    test_pereval_data["images"] = [photo()]
    pereval_id = create(client, test_pereval_data)
    assert client.patch(f"/submitData/{pereval_id}", json={"images": []}).status_code == 200
    with Session(db.get_bind()) as fresh:
        assert fresh.query(Image).count() == 1 and fresh.get(Image, orphan_id) is not None


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("img", ["a", "!!!!", "", "YQ==", "YR==", "YQ===", "YQ==\n", "_w==",
                                 "data:image/png;base64,YQ=="])
def test_invalid_base64_and_non_images_rejected_before_writes(client, db, test_pereval_data, method, img):
    if method == "patch":
        test_pereval_data["images"] = [photo()]
        pereval_id = create(client, test_pereval_data)
        before = client.get(f"/submitData/{pereval_id}").json()
        response = client.patch(f"/submitData/{pereval_id}", json={
            "title": "Must roll back", "images": [{"title": "Bad", "img": img}],
        })
    else:
        test_pereval_data["images"] = [{"title": "Bad", "img": img}]
        response = client.post("/submitData/", json=test_pereval_data)
    error(response, 422, "VALIDATION_ERROR")
    with Session(db.get_bind()) as fresh:
        if method == "patch":
            assert fresh.get(PerevalAdded, pereval_id).title == before["title"]
            assert fresh.query(Image).one().img == base64.b64decode(before["images"][0]["img"])
        else:
            assert all(fresh.query(model).count() == 0 for model in (User, Coords, Image, PerevalAdded, PerevalImages))


@pytest.mark.parametrize("method", ["post", "patch"])
def test_database_failure_rolls_back_and_hides_exception(client, db, test_pereval_data, caplog, method):
    test_pereval_data["images"] = [photo()]
    pereval_id = create(client, test_pereval_data) if method == "patch" else None
    secret = "postgresql://private:password@db/secret SELECT private_request"

    def fail_image_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO images"):
            raise RuntimeError(secret)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", fail_image_insert)
    try:
        # The ordinary client re-raises escaped exceptions, just as an ASGI server
        # would receive them. A handled 500 must not escape to Uvicorn's logger.
        with caplog.at_level(logging.ERROR):
            if method == "patch":
                response = client.patch(f"/submitData/{pereval_id}", json={
                    "title": "private_request", "coords": {"height": 3400},
                    "level": {"spring": "2B"}, "images": [photo("blue")],
                })
            else:
                response = client.post("/submitData/", json=test_pereval_data)
        error(response, 500, "INTERNAL_ERROR")
        logs = [record for record in caplog.records if record.name == "app.errors"]
        assert len(logs) == 1 and logs[0].exc_info is None and logs[0].stack_info is None
        assert secret not in caplog.text and "private_request" not in caplog.text
        assert "password" not in response.text and "SELECT" not in response.text
    finally:
        event.remove(engine, "before_cursor_execute", fail_image_insert)
    with Session(engine) as fresh:
        if method == "patch":
            row = fresh.get(PerevalAdded, pereval_id)
            assert row.title == test_pereval_data["title"] and row.coords.height == 3200
            assert row.level_spring is None
            assert fresh.query(Image).one().img == base64.b64decode(test_pereval_data["images"][0]["img"])
            assert fresh.query(PerevalImages).count() == 1
        else:
            assert all(fresh.query(model).count() == 0 for model in (User, Coords, Image, PerevalAdded, PerevalImages))


def test_router_and_validation_errors_are_safe(client):
    error(client.get("/missing"), 404, "ROUTE_NOT_FOUND")
    response = client.delete("/submitData/1")
    error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"]
    detail = error(client.post("/submitData/", content='{"broken":',
                               headers={"Content-Type": "application/json"}), 422, "VALIDATION_ERROR")
    assert detail["errors"][0]["loc"] == ["body"]
    assert detail["errors"][0]["type"] == "json_invalid"


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("variant", ["newline", "space", "padding", "extra_padding", "pad_bits", "data_url", "urlsafe"])
def test_strict_base64_with_otherwise_valid_files(client, test_pereval_data, method, variant):
    image = photo(format="JPEG" if variant == "urlsafe" else "PNG")
    encoded = image["img"]
    if variant == "newline":
        image["img"] += "\n"
    elif variant == "space":
        image["img"] = encoded[:20] + " " + encoded[20:]
    elif variant == "padding":
        assert encoded.endswith("=")
        image["img"] = encoded.rstrip("=")
    elif variant == "extra_padding":
        image["img"] += "="
    elif variant == "pad_bits":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        index = len(encoded.rstrip("=")) - 1
        replacement = alphabet[alphabet.index(encoded[index]) + 1]
        image["img"] = encoded[:index] + replacement + encoded[index + 1:]
        assert base64.b64decode(image["img"], validate=True) == base64.b64decode(encoded)
    elif variant == "data_url":
        image["img"] = "data:image/png;base64," + encoded
    else:
        image["img"] = base64.urlsafe_b64encode(base64.b64decode(encoded)).decode("ascii")
        assert image["img"] != encoded
    if method == "post":
        test_pereval_data["images"] = [image]
        response = client.post("/submitData/", json=test_pereval_data)
    else:
        pereval_id = create(client, test_pereval_data)
        response = client.patch(f"/submitData/{pereval_id}", json={"images": [image]})
    error(response, 422, "VALIDATION_ERROR")


def test_corrupted_stored_image_is_not_silently_hidden(client, db, test_pereval_data, caplog):
    test_pereval_data["images"] = [photo()]
    pereval_id = create(client, test_pereval_data)
    db.query(Image).one().img = b"corrupt legacy data"
    db.commit()
    with caplog.at_level(logging.ERROR):
        response = client.get(f"/submitData/{pereval_id}")
    error(response, 500, "INTERNAL_ERROR")
    assert any(record.name == "app.errors" for record in caplog.records)
    assert "corrupt legacy data" not in caplog.text
