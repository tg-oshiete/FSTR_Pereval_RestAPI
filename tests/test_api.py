import pytest


def test_create_and_get_pereval(client, test_pereval_data):
    response = client.post("/submitData/", json=test_pereval_data)
    assert response.status_code == 201
    data = response.json()
    pereval_id = data["id"]

    response = client.get(f"/submitData/{pereval_id}")
    assert response.status_code == 200
    pereval = response.json()

    # Проверяем данные
    assert pereval["id"] == pereval_id
    assert pereval["title"] == "Тестовый перевал"
    assert pereval["status"] == "new"
    assert pereval["user"]["email"] == "string@example.com"


def test_update_pereval(client, test_pereval_data):
    response = client.post("/submitData/", json=test_pereval_data)
    pereval_id = response.json()["id"]
    update_data = {"title": "Обновленное название"}
    response = client.patch(f"/submitData/{pereval_id}", json=update_data)

    assert response.status_code == 200
    response = client.get(f"/submitData/{pereval_id}")
    assert response.json()["title"] == "Обновленное название"


def test_get_perevals_by_email(client, test_pereval_data):
    client.post("/submitData/", json=test_pereval_data)
    response = client.get("/submitData/?user__email=string@example.com")

    assert response.status_code == 200
    perevals = response.json()

    assert len(perevals) > 0
    assert perevals[0]["user_email"] == "string@example.com"