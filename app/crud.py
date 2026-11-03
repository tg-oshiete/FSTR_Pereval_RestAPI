"""Read queries; transaction ownership for writes lives in services.py."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Coords, Image, PerevalAdded, PerevalImages, User


def get_pereval(db: Session, pereval_id: int) -> PerevalAdded | None:
    return db.scalar(select(PerevalAdded).options(
        joinedload(PerevalAdded.user), joinedload(PerevalAdded.coords),
    ).where(PerevalAdded.id == pereval_id))


def get_images(db: Session, pereval_id: int) -> list[Image]:
    return list(db.scalars(select(Image).join(PerevalImages).where(
        PerevalImages.id_pereval == pereval_id,
    ).order_by(PerevalImages.position, PerevalImages.id)))


def get_perevals_by_email(db: Session, email: str, *, limit: int = 20, offset: int = 0):
    # Select only the list's columns: no binary images or full profiles.
    return db.execute(select(
        PerevalAdded.id, PerevalAdded.title, PerevalAdded.status, PerevalAdded.date_added,
        User.email.label("user_email"), Coords.latitude, Coords.longitude, Coords.height,
    ).join(User).join(Coords).where(User.email == email).order_by(
        PerevalAdded.id,
    ).limit(limit).offset(offset)).mappings().all()
