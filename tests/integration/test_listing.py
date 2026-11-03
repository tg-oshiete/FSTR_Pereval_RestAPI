import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import User


pytestmark = pytest.mark.integration


def test_bounded_stable_list_without_photo_queries(pg_client, pg_engine, test_pereval_data):
    # Cross both the default page size and the maximum allowed client page size.
    ids = [pg_client.post("/submitData/", json=test_pereval_data).json()["id"] for _ in range(103)]
    queries = []

    def record(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(pg_engine, "before_cursor_execute", record)
    try:
        params = {"user__email": "string@example.com"}
        default = pg_client.get("/submitData/", params=params).json()
        first = pg_client.get("/submitData/", params={**params, "limit": 100, "offset": 0}).json()
        tail = pg_client.get("/submitData/", params={**params, "limit": 100, "offset": 100}).json()
        repeated = pg_client.get("/submitData/", params={**params, "limit": 100, "offset": 0}).json()
        assert [row["id"] for row in default] == ids[:20]
        assert [row["id"] for row in first + tail] == ids
        assert first == repeated
        assert set(default[0]) == {"id", "title", "status", "date_added", "user_email", "latitude", "longitude", "height"}
        assert pg_client.get("/submitData/", params={**params, "offset": 2147483647}).json() == []
    finally:
        event.remove(pg_engine, "before_cursor_execute", record)
    lists = [query for query in queries if "FROM pereval_added" in query]
    assert len(queries) == 10 and len(lists) == 5  # Auth query plus bounded list.
    assert all("images" not in query.lower() and "LIMIT" in query and "ORDER BY pereval_added.id" in query for query in lists)


@pytest.mark.parametrize("key,value", [("limit", 0), ("limit", 101), ("limit", -1), ("limit", "1.5"),
                                       ("offset", -1), ("offset", 2147483648), ("offset", "oops")])
def test_invalid_pagination(pg_client, key, value):
    response = pg_client.get("/submitData/", params={"user__email": "string@example.com", key: value})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["loc"] == ["query", key]


def test_openapi_documents_pagination(pg_client):
    params = {field["name"]: field["schema"] for field in
              pg_client.get("/openapi.json").json()["paths"]["/submitData/"]["get"]["parameters"]}
    assert {key: params["limit"][key] for key in ("default", "minimum", "maximum")} == {
        "default": 20, "minimum": 1, "maximum": 100,
    }
    assert params["offset"]["default"] == 0 and params["offset"]["maximum"] == 2147483647


def test_existing_profile_omitted_fields_and_explicit_conflicts(pg_client, pg_engine, test_pereval_data):
    test_pereval_data["user"].update(phone="123", otc="Тестович")
    with Session(pg_engine) as operator:
        user = operator.query(User).one()
        user.phone, user.otc = "123", "Тестович"
        operator.commit()
    assert pg_client.post("/submitData/", json=test_pereval_data).status_code == 201
    test_pereval_data["user"].pop("phone")
    test_pereval_data["user"].pop("otc")
    assert pg_client.post("/submitData/", json=test_pereval_data).status_code == 201
    for field, value in (("phone", None), ("otc", "Другой"), ("fam", "Другой"), ("name", "Другой")):
        response = pg_client.post("/submitData/", json={**test_pereval_data,
            "user": {**test_pereval_data["user"], field: value}})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "USER_PROFILE_CONFLICT"
    with Session(pg_engine) as fresh:
        user = fresh.query(User).one()
        assert user.phone == "123" and user.otc == "Тестович" and len(user.perevals) == 2
