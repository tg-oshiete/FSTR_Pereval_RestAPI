"""Read queries; transaction ownership for writes lives in services.py."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Coords, DecisionHistory, Image, PerevalAdded, PerevalImages, User


def get_pereval(db: Session, pereval_id: int) -> PerevalAdded | None:
    return db.scalar(select(PerevalAdded).options(
        joinedload(PerevalAdded.user), joinedload(PerevalAdded.coords),
    ).where(PerevalAdded.id == pereval_id))


def get_images(db: Session, pereval_id: int) -> list[Image]:
    return list(db.scalars(select(Image).join(PerevalImages).where(
        PerevalImages.id_pereval == pereval_id,
    ).order_by(PerevalImages.position, PerevalImages.id)))


def list_query():
    # Select only the list's columns: no binary images or full profiles.
    return select(
        PerevalAdded.id, PerevalAdded.title, PerevalAdded.status, PerevalAdded.date_added,
        User.email.label("user_email"), Coords.latitude, Coords.longitude, Coords.height,
    ).join(User).join(Coords).order_by(PerevalAdded.id)


def get_perevals_by_email(db: Session, email: str, *, limit: int = 20, offset: int = 0):
    return db.execute(list_query().where(User.email == email).limit(limit).offset(offset)).mappings().all()


def moderation_queue(db: Session, status: str, *, limit: int, offset: int):
    return db.execute(list_query().where(PerevalAdded.status == status).limit(limit).offset(offset)).mappings().all()


def get_history(db: Session, pereval_id: int, *, limit: int, offset: int):
    return db.scalars(select(DecisionHistory).where(
        DecisionHistory.pereval_id == pereval_id,
    ).order_by(DecisionHistory.id).limit(limit).offset(offset)).all()
