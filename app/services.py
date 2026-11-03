"""One explicit transaction per submission write; no HTTP or response objects.

Call with a fresh Session (before any SELECT/autobegin). A returned ID is already
committed; exceptions, including flush/commit failures, leave no partial write.
"""

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Coords, Image, PerevalAdded, PerevalImages, User
from app.schemas import PerevalCreate, PerevalUpdate, UserCreate


class PerevalNotFound(Exception):
    pass


class PerevalNotEditable(Exception):
    pass


class UserProfileConflict(Exception):
    pass


def get_or_create_user(db: Session, profile: UserCreate) -> User:
    user = db.scalar(select(User).where(User.email == profile.email))
    if user is None:
        try:
            # Only this attempted insert is rolled back on an email collision;
            # PostgreSQL's outer transaction stays usable after the SAVEPOINT.
            with db.begin_nested():
                user = User(**profile.model_dump())
                db.add(user)
                db.flush()
        except IntegrityError:
            user = db.scalar(select(User).where(User.email == profile.email))
            if user is None:
                raise  # Not the expected email collision: preserve the real error.
    if any(getattr(user, field) != value
           for field, value in profile.model_dump(exclude_unset=True).items()):
        raise UserProfileConflict()
    return user


def add_images(db: Session, pereval_id: int, images) -> None:
    for position, data in enumerate(images):
        image = Image(img=data.decoded_bytes, title=data.title)
        db.add(image)
        db.flush()
        db.add(PerevalImages(id_pereval=pereval_id, id_image=image.id, position=position))
    db.flush()


def create_pereval(db: Session, data: PerevalCreate) -> int:
    with db.begin():
        user = get_or_create_user(db, data.user)
        coords = Coords(**data.coords.model_dump())
        db.add(coords)
        db.flush()
        pereval = PerevalAdded(
            **data.model_dump(exclude={"user", "coords", "level", "images"}),
            **{f"level_{season}": value for season, value in data.level.model_dump().items()},
            user_id=user.id, coord_id=coords.id, status="new",
        )
        db.add(pereval)
        db.flush()
        add_images(db, pereval.id, data.images)
        pereval_id = pereval.id  # Read before commit/expiration; no new transaction.
    return pereval_id


def update_pereval(db: Session, pereval_id: int, data: PerevalUpdate) -> None:
    with db.begin():
        pereval = db.scalar(select(PerevalAdded).where(
            PerevalAdded.id == pereval_id,
        ).with_for_update())
        if pereval is None:
            raise PerevalNotFound()
        if pereval.status != "new":
            raise PerevalNotEditable()

        updates = data.model_dump(exclude_unset=True)
        for field in ("title", "beauty_title", "other_titles", "connect", "add_time"):
            if field in updates:
                setattr(pereval, field, updates[field])
        for field, value in updates.get("coords", {}).items():
            setattr(pereval.coords, field, value)
        for season, value in updates.get("level", {}).items():
            setattr(pereval, f"level_{season}", value)

        if "images" in updates:
            old_ids = list(db.scalars(select(PerevalImages.id_image).where(
                PerevalImages.id_pereval == pereval_id,
            )))
            db.query(PerevalImages).filter_by(id_pereval=pereval_id).delete(
                synchronize_session=False,
            )
            if old_ids:
                db.query(Image).filter(
                    Image.id.in_(old_ids),
                    ~exists().where(PerevalImages.id_image == Image.id),
                ).delete(synchronize_session=False)
            add_images(db, pereval_id, data.images)
        db.flush()
