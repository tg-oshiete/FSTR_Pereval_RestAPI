"""Shared POST/PATCH constraints, inclusive limits and the published contract."""

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
import re
import struct
import zlib

from PIL import Image as PILImage
from PIL.PngImagePlugin import PngInfo
import pytest
from sqlalchemy.orm import Session

from app.models import PerevalAdded, User
from app.schemas import UserCreate
from tests.test_regressions import create, error, photo


def limits(test_app, **values):
    test_app.state.settings = test_app.state.settings.model_copy(update=values)


def operator_profile(db, profile):
    # Validation tests provision their chosen profile outside the HTTP API.
    with Session(db.get_bind()) as operator:
        user = operator.query(User).one()
        for field, value in UserCreate.model_validate(profile).model_dump().items():
            setattr(user, field, value)
        operator.commit()


def send(client, data, method, payload):
    if method == "post":
        body = deepcopy(data)
        body.update(payload)
        return client.post("/submitData/", json=body)
    pereval_id = create(client, data)
    return client.patch(f"/submitData/{pereval_id}", json=payload)


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("field,bound", [("title", 255), ("beauty_title", 255),
                                         ("other_titles", 1024), ("connect", 2000)])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_text_length_boundaries(client, test_pereval_data, method, field, bound, delta):
    response = send(client, test_pereval_data, method, {field: " я" + "я" * (bound + delta - 1) + " "})
    if delta > 0:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == (201 if method == "post" else 200), response.text


@pytest.mark.parametrize("field,bound", [("fam", 100), ("name", 100), ("otc", 100), ("phone", 32)])
@pytest.mark.parametrize("delta", [0, 1])
def test_profile_length_boundaries(client, db, test_pereval_data, field, bound, delta):
    test_pereval_data["user"][field] = "x" * (bound + delta)
    if not delta:
        operator_profile(db, test_pereval_data["user"])
    response = client.post("/submitData/", json=test_pereval_data)
    if delta:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == 201, response.text


@pytest.mark.parametrize("payload", [
    {"title": "Title\x00"}, {"title": "A\nB"}, {"beauty_title": ""}, {"other_titles": "   "},
    {"connect": "A\rB"}, {"title": "A\x7fB"}, {"images": [{"title": " ", "img": photo()["img"]}]},
    {"level": {"spring": "3C"}}, {"level": {"winter": "4A"}}, {"level": {"summer": ""}},
    {"add_time": "2026-10-31 12:00:00"}, {"add_time": "2026-10-31"}, {"add_time": 1793448000},
    {"add_time": "2026-10-31T15:00:00.1234567Z"}, {"add_time": "2026-10-31T15:00:00+03:99"},
    {"add_time": "0001-01-01T00:00:00+03:00"}, {"add_time": "9999-12-31T23:00:00-03:00"},
    {"coords": {"latitude": True, "longitude": 20, "height": 3000}},
])
@pytest.mark.parametrize("method", ["post", "patch"])
def test_shared_invalid_fields(client, test_pereval_data, method, payload):
    error(send(client, test_pereval_data, method, payload), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(extra=1),
    lambda data: data.update(status="accepted"),
    lambda data: data["user"].update(role="moderator"),
    lambda data: data["coords"].update(id=7),
    lambda data: data["level"].update(unknown="1A"),
    lambda data: data.update(user=None),
    lambda data: data.update(add_time=None),
    lambda data: data.update(images=None),
])
def test_post_unknown_fields_and_nulls(client, test_pereval_data, mutate):
    mutate(test_pereval_data)
    error(client.post("/submitData/", json=test_pereval_data), 422, "VALIDATION_ERROR")


def test_normalized_values_round_trip(client, db, test_pereval_data):
    test_pereval_data.update(title="  Перевал  А  ", connect="Route\n\tApproach",
                             add_time="2026-10-31T15:00:00.123456+03:00")
    test_pereval_data["user"].update(email="  Local.Part@EXAMPLE.COM ", fam=" Иванов ")
    operator_profile(db, test_pereval_data["user"])
    test_pereval_data["level"] = {"spring": " 1а ", "summer": "2Б", "winter": "Н/К", "autumn": "3b"}
    pereval_id = create(client, test_pereval_data)
    result = client.get(f"/submitData/{pereval_id}")
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["title"] == "Перевал  А"
    assert result["user"]["fam"] == "Иванов"
    assert result["user"]["email"] == "Local.Part@example.com"
    assert result["level"] == {"spring": "1A", "summer": "2B", "winter": "n/k", "autumn": "3B"}
    assert result["add_time"] == "2026-10-31T12:00:00.123456Z"
    assert result["date_added"].endswith("Z")
    listed = client.get("/submitData/", params={"user__email": " Local.Part@EXAMPLE.COM "})
    assert listed.status_code == 200 and listed.json()[0]["id"] == pereval_id
    assert listed.json()[0]["date_added"].endswith("Z")
    assert client.patch(f"/submitData/{pereval_id}", json={
        "level": {"summer": " н/к "}, "add_time": "2026-11-01T00:00:00-03:00",
    }).status_code == 200
    updated = client.get(f"/submitData/{pereval_id}").json()
    assert updated["level"]["summer"] == "n/k" and updated["level"]["spring"] == "1A"
    assert updated["add_time"] == "2026-11-01T03:00:00Z"


@pytest.mark.parametrize("email", ["not-an-email", "", "a\x00@example.com", "a" * 255 + "@example.com"])
def test_email_query_rejects_invalid_values(client, email):
    error(client.get("/submitData/", params={"user__email": email}), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("value", ["0", "-1", "2147483648", "1.5", "1.0", "not-id"])
@pytest.mark.parametrize("method", ["get", "patch"])
def test_public_id_bounds(client, value, method):
    kwargs = {"json": {"title": "Valid"}} if method == "patch" else {}
    error(getattr(client, method)(f"/submitData/{value}", **kwargs), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("count", [4, 5, 6])
def test_image_count_boundary(client, test_pereval_data, method, count):
    response = send(client, test_pereval_data, method, {"images": [photo()] * count})
    if count > 5:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == (201 if method == "post" else 200), response.text


def padded_png(padding):
    info = PngInfo()
    info.add_text("padding", "x" * padding)
    return photo(pnginfo=info)


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_decoded_image_byte_boundary(client, test_app, test_pereval_data, method, delta):
    bound = len(base64.b64decode(padded_png(1000)["img"]))
    limits(test_app, max_image_bytes=bound)
    image = padded_png(1000 + delta)
    assert len(base64.b64decode(image["img"])) == bound + delta
    response = send(client, test_pereval_data, method, {"images": [image]})
    if delta > 0:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == (201 if method == "post" else 200), response.text


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_total_image_byte_boundary(client, test_app, test_pereval_data, method, delta):
    image = photo()
    size = len(base64.b64decode(image["img"])) * 2
    limits(test_app, max_images_total_bytes=size - delta)
    response = send(client, test_pereval_data, method, {"images": [image, image]})
    if delta > 0:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == (201 if method == "post" else 200), response.text


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("setting", ["max_image_pixels", "max_image_dimension"])
def test_image_geometry_boundary(client, test_app, test_pereval_data, method, delta, setting):
    limits(test_app, **{setting: 12})
    response = send(client, test_pereval_data, method, {"images": [photo(size=(1, 12 + delta))]})
    if delta > 0:
        error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == (201 if method == "post" else 200), response.text


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("format", ["JPEG", "PNG", "PNG_METADATA", "GIF", "WEBP", "APNG", "APNG_SINGLE", "BAD_APNG", "TRUNCATED", "BAD_CRC"])
def test_real_file_validation(client, test_pereval_data, method, format):
    if format == "APNG":
        output = BytesIO()
        with PILImage.new("RGB", (2, 2), "red") as first, PILImage.new("RGB", (2, 2), "blue") as second:
            first.save(output, format="PNG", save_all=True, append_images=[second], loop=0)
        image = {"title": "Animated", "img": base64.b64encode(output.getvalue()).decode("ascii")}
    elif format in {"BAD_APNG", "APNG_SINGLE"}:
        image = photo()
        data = base64.b64decode(image["img"])

        def chunk(kind, payload):
            content = kind + payload
            return struct.pack(">I", len(payload)) + content + struct.pack(">I", zlib.crc32(content))

        # Pillow's writer optimizes one frame to a plain PNG, so construct the
        # valid APNG chunks explicitly. BAD_APNG has an invalid zero frame count.
        animation = chunk(b"acTL", struct.pack(">II", 1 if format == "APNG_SINGLE" else 0, 0))
        if format == "APNG_SINGLE":
            animation += chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 2, 2, 0, 0, 1, 100, 0, 0))
        data = data[:33] + animation + data[33:]
        if format == "APNG_SINGLE":
            with PILImage.open(BytesIO(data)) as decoded:
                assert decoded.n_frames == 1 and decoded.get_format_mimetype() == "image/apng"
        image["img"] = base64.b64encode(data).decode("ascii")
    elif format == "PNG_METADATA":
        info = PngInfo()
        info.add_text("loop", "ordinary textual metadata")
        image = photo(pnginfo=info)
    elif format == "TRUNCATED":
        image = photo(format="JPEG")
        image["img"] = base64.b64encode(base64.b64decode(image["img"])[:-5]).decode("ascii")
    elif format == "BAD_CRC":
        image = photo()
        data = bytearray(base64.b64decode(image["img"]))
        data[-5] ^= 1
        image["img"] = base64.b64encode(data).decode("ascii")
    else:
        image = photo(format=format)
    response = send(client, test_pereval_data, method, {"images": [image]})
    if format in {"JPEG", "PNG", "PNG_METADATA"}:
        assert response.status_code == (201 if method == "post" else 200), response.text
    else:
        error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("method", ["post", "patch"])
def test_request_byte_limit(client, test_app, db, test_pereval_data, delta, streamed, method):
    pereval_id = create(client, test_pereval_data) if method == "patch" else None
    path = f"/submitData/{pereval_id}" if method == "patch" else "/submitData/"
    payload = {"title": "Новое название"} if method == "patch" else test_pereval_data
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    limits(test_app, max_request_bytes=len(body) - delta)
    if streamed:
        # Actual ASGI chunks without Content-Length, not TestClient's coalesced iterator.
        async def run():
            chunks = iter([body[:11], body[11:53], body[53:]])
            messages = []

            async def receive():
                chunk = next(chunks)
                return {"type": "http.request", "body": chunk, "more_body": len(chunk) != len(body[53:])}

            async def transmit(message):
                messages.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": method.upper(), "scheme": "http", "path": path, "raw_path": path.encode(),
                     "root_path": "", "query_string": b"", "headers": [
                         (b"content-type", b"application/json"),
                         (b"authorization", client.headers["Authorization"].encode("ascii")),
                     ],
                     "client": ("testclient", 50000), "server": ("testserver", 80)}
            await test_app(scope, receive, transmit)
            return messages

        messages = asyncio.run(run())
        actual_status = messages[0]["status"]
        result = json.loads(b"".join(message.get("body", b"") for message in messages[1:]))
    else:
        response = getattr(client, method)(path, content=body, headers={"Content-Type": "application/json"})
        actual_status, result = response.status_code, response.json()
    if delta > 0:
        assert actual_status == 413
        assert result["detail"]["code"] == "PAYLOAD_TOO_LARGE"
        with Session(db.get_bind()) as fresh:
            assert fresh.query(PerevalAdded).count() == (1 if method == "patch" else 0)
            if method == "patch":
                assert fresh.get(PerevalAdded, pereval_id).title == test_pereval_data["title"]
    else:
        assert actual_status == (200 if method == "patch" else 201), result


def test_openapi_error_and_success_contract(client):
    document = client.get("/openapi.json").json()
    routes = document["paths"]
    for path, method, success, success_schema in [
        ("/submitData/", "post", "201", "SubmitResponse"),
        ("/submitData/{pereval_id}", "patch", "200", "UpdateResponse"),
        ("/submitData/{pereval_id}", "get", "200", "PerevalResponse"),
        ("/submitData/", "get", "200", None),
    ]:
        responses = routes[path][method]["responses"]
        assert "204" not in responses and "400" not in responses
        assert {"422", "500"} <= responses.keys()
        assert responses["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ValidationErrorResponse")
        if success_schema:
            assert responses[success]["content"]["application/json"]["schema"]["$ref"].endswith("/" + success_schema)
    assert "409" in routes["/submitData/{pereval_id}"]["patch"]["responses"]
    assert document["components"]["schemas"]["SubmitResponse"]["properties"]["status"]["const"] == 201
    assert document["components"]["schemas"]["UpdateResponse"]["properties"]["state"]["const"] == 1


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_coordinates(client, test_pereval_data, method, value):
    if method == "patch":
        pereval_id = create(client, test_pereval_data)
        path = f"/submitData/{pereval_id}"
        body = '{"coords":{"latitude":' + value + '}}'
    else:
        path = "/submitData/"
        test_pereval_data["coords"]["latitude"] = float(value)
        body = json.dumps(test_pereval_data)
    error(getattr(client, method)(path, content=body, headers={"Content-Type": "application/json"}),
          422, "VALIDATION_ERROR")


@pytest.mark.parametrize("coords", [
    {"latitude": -90, "longitude": -180, "height": 1},
    {"latitude": 90, "longitude": 180, "height": 10000},
])
def test_coordinate_inclusive_bounds_and_integer_round_trip(client, test_pereval_data, coords):
    test_pereval_data["coords"] = coords
    pereval_id = create(client, test_pereval_data)
    result = client.get(f"/submitData/{pereval_id}").json()
    assert result["coords"] == coords
    assert type(result["coords"]["height"]) is int


@pytest.mark.parametrize("length", [254, 255])
def test_email_length_boundary_for_post_and_query(client, db, test_pereval_data, length):
    email = "l" * 64 + "@" + "a" * 63 + "." + "b" * 63 + "." + "c" * (length - 197) + ".com"
    assert len(email) == length
    test_pereval_data["user"]["email"] = email
    if length == 254:
        operator_profile(db, test_pereval_data["user"])
    post = client.post("/submitData/", json=test_pereval_data)
    query = client.get("/submitData/", params={"user__email": email})
    if length == 254:
        assert post.status_code == 201 and query.status_code == 200
        assert query.json()[0]["user_email"] == email
    else:
        error(post, 422, "VALIDATION_ERROR")
        error(query, 422, "VALIDATION_ERROR")


def test_profile_reuse_compares_only_supplied_optional_fields(client, db, test_pereval_data):
    from app.models import User

    test_pereval_data["user"].update(phone="+7 000 000", otc="Отчество")
    operator_profile(db, test_pereval_data["user"])
    create(client, test_pereval_data)
    del test_pereval_data["user"]["phone"]
    del test_pereval_data["user"]["otc"]
    second = create(client, test_pereval_data)
    assert client.get(f"/submitData/{second}").json()["user"]["phone"] == "+7 000 000"
    test_pereval_data["user"]["phone"] = None
    error(client.post("/submitData/", json=test_pereval_data), 409, "USER_PROFILE_CONFLICT")
    with Session(db.get_bind()) as fresh:
        assert fresh.query(PerevalAdded).count() == 2 and fresh.query(User).count() == 1
        assert fresh.query(User).one().phone == "+7 000 000"


def test_openapi_uses_configured_limits_and_exact_response_fields(client, test_app):
    limits(test_app, max_images_per_pereval=2, max_image_bytes=300, max_request_bytes=5000)
    test_app.openapi_schema = None
    schema = client.get("/openapi.json").json()
    models = schema["components"]["schemas"]
    assert schema["info"]["x-upload-limits"]["MAX_REQUEST_BYTES"] == 5000
    assert models["PerevalCreate"]["properties"]["images"]["maxItems"] == 2
    assert models["ImageCreate"]["properties"]["img"]["maxLength"] == 400
    assert set(models["PerevalResponse"]["required"]) == set(models["PerevalResponse"]["properties"])
    assert models["PerevalUpdate"]["minProperties"] == 1
    assert "default" not in models["PerevalUpdate"]["properties"]["title"]
    accepted_difficulty = models["LevelCreate"]["properties"]["spring"]["anyOf"][0]
    assert re.fullmatch(accepted_difficulty["pattern"], " 1а ")
    assert models["LevelResponse"]["properties"]["spring"]["anyOf"][0]["enum"] == ["n/k", "1A", "1B", "2A", "2B", "3A", "3B"]


def test_error_payloads_validate_against_published_models(client, test_pereval_data):
    from app.schemas import ErrorResponse, PerevalResponse, SubmitResponse, ValidationErrorResponse

    response = client.post("/submitData/", json=test_pereval_data)
    submitted = SubmitResponse.model_validate(response.json())
    PerevalResponse.model_validate(client.get(f"/submitData/{submitted.id}").json())
    ErrorResponse.model_validate(client.get("/submitData/2147483647").json())
    invalid = client.patch(f"/submitData/{submitted.id}", json={"user": {"email": "private@invalid"}})
    ValidationErrorResponse.model_validate(invalid.json())
    assert "private@invalid" not in invalid.text
