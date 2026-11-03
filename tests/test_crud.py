from app import crud, services
from app.schemas import PerevalCreate, PerevalUpdate
from app.models import PerevalAdded

def test_create_pereval(db, test_pereval_data, identity):
    pereval_data = PerevalCreate(**test_pereval_data)
    pereval_id = services.create_pereval(db, pereval_data, identity["principal"])
    result = db.get(PerevalAdded, pereval_id)

    assert result.id is not None
    assert result.title == "Тестовый перевал"
    assert result.status == "new"


def test_get_pereval(db, test_pereval_data, identity):
    pereval_data = PerevalCreate(**test_pereval_data)
    pereval_id = services.create_pereval(db, pereval_data, identity["principal"])
    result = crud.get_pereval(db, pereval_id)
    assert result is not None
    assert result.title == "Тестовый перевал"



def test_update_pereval(db, test_pereval_data, identity):
    pereval_data = PerevalCreate(**test_pereval_data)
    pereval_id = services.create_pereval(db, pereval_data, identity["principal"])
    update_data = PerevalUpdate.model_validate({"title": "Обновленное название"})
    services.update_pereval(db, pereval_id, update_data, identity["principal"])
    assert not db.in_transaction()

    pereval_in_db = db.query(PerevalAdded).filter(PerevalAdded.id == pereval_id).first()
    assert pereval_in_db is not None
    assert pereval_in_db.title == "Обновленное название"

def test_get_perevals_by_email(db, test_pereval_data, identity):
    pereval_data = PerevalCreate(**test_pereval_data)
    services.create_pereval(db, pereval_data, identity["principal"])

    result = crud.get_perevals_by_email(db, "string@example.com")

    assert len(result) > 0
    assert result[0]["user_email"] == "string@example.com"
