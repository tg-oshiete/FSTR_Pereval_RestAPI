import pytest
from app.crud import PerevalRepository
from app.schemas import PerevalCreate, PerevalUpdate
from app.models import PerevalAdded

def test_create_pereval(db, test_pereval_data):
    pereval_data = PerevalCreate(**test_pereval_data)
    result = PerevalRepository.create_pereval(db, pereval_data)

    assert result.id is not None
    assert result.title == "Тестовый перевал"
    assert result.status == "new"


def test_get_pereval(db, test_pereval_data):
    pereval_data = PerevalCreate(**test_pereval_data)
    pereval = PerevalRepository.create_pereval(db, pereval_data)
    pereval_id = pereval.id
    result = PerevalRepository.get_pereval_or_404(db, pereval.id)
    assert result is not None
    assert result.model_dump()['title'] == "Тестовый перевал"



def test_update_pereval(db, test_pereval_data):
    pereval_data = PerevalCreate(**test_pereval_data)
    pereval = PerevalRepository.create_pereval(db, pereval_data)
    pereval_id = pereval.id
    update_data = PerevalUpdate.model_validate({"title": "Обновленное название"})
    result = PerevalRepository.update_pereval(db, pereval.id, update_data)

    assert result["state"] == 1, f"Обновление не удалось: {result}"

    pereval_in_db = db.query(PerevalAdded).filter(PerevalAdded.id == pereval_id).first()
    assert pereval_in_db is not None
    assert pereval_in_db.title == "Обновленное название"

def test_get_perevals_by_email(db, test_pereval_data):
    pereval_data = PerevalCreate(**test_pereval_data)
    PerevalRepository.create_pereval(db, pereval_data)

    result = PerevalRepository.get_perevals_by_email(db, "string@example.com")

    assert len(result) > 0
    assert result[0]["user_email"] == "string@example.com"