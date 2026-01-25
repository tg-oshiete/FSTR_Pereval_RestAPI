from sqlalchemy.orm import Session
from app.schemas import PerevalCreate
from app.models import PerevalAdded


class PerevalRepository:
    @staticmethod
    def create_pereval(db:Session, pereval_data: PerevalCreate) -> PerevalAdded:
        pass
