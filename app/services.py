"""One explicit transaction per submission write; no HTTP or response objects.

Call with a fresh Session (before any SELECT/autobegin). A returned ID is already
committed; exceptions, including flush/commit failures, leave no partial write.
"""

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import Coords, DecisionHistory, Image, PerevalAdded, PerevalImages, User
from app.schemas import PerevalCreate, PerevalUpdate, StatusUpdate
from app.security import AccessDenied, Principal, Role, require_owner, require_role


class PerevalNotFound(Exception):
    pass


class PerevalNotEditable(Exception):
    pass


class UserProfileConflict(Exception):
    pass


class StatusConflict(Exception):
    pass


class InvalidStatusTransition(Exception):
    pass


def add_images(db: Session, pereval_id: int, images) -> None:
    for position, data in enumerate(images):
        image = Image(img=data.decoded_bytes, title=data.title)
        db.add(image)
        db.flush()
        db.add(PerevalImages(id_pereval=pereval_id, id_image=image.id, position=position))
    db.flush()


def create_pereval(db: Session, data: PerevalCreate, principal: Principal) -> int:
    require_role(principal, Role.AUTHOR)
    if data.user.email != principal.email:
        raise AccessDenied()
    with db.begin():
        # Identity is provisioned by CLI, never chosen/created by this request.
        user = db.get(User, principal.user_id)
        if user is None or user.email != data.user.email:
            raise AccessDenied()
        if any(getattr(user, field) != value
               for field, value in data.user.model_dump(exclude_unset=True).items()):
            raise UserProfileConflict()
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


def update_pereval(db: Session, pereval_id: int, data: PerevalUpdate, principal: Principal) -> None:
    require_role(principal, Role.AUTHOR)
    with db.begin():
        pereval = db.scalar(select(PerevalAdded).where(
            PerevalAdded.id == pereval_id,
        ).with_for_update())
        if pereval is None:
            raise PerevalNotFound()
        require_owner(principal, pereval.user_id, moderator_allowed=False)
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


TRANSITIONS = {("new", "pending"), ("pending", "accepted"), ("pending", "rejected")}


def change_status(db: Session, pereval_id: int, data: StatusUpdate, principal: Principal) -> None:
    require_role(principal, Role.MODERATOR)
    with db.begin():
        # Same row lock as content PATCH. READ COMMITTED rechecks the latest row
        # after a competing writer commits, before state/ownership are inspected.
        pereval = db.scalar(select(PerevalAdded).where(
            PerevalAdded.id == pereval_id,
        ).with_for_update())
        if pereval is None:
            raise PerevalNotFound()
        if pereval.status != data.expected_status:
            raise StatusConflict()
        if (pereval.status, data.status) not in TRANSITIONS:
            raise InvalidStatusTransition()
        db.add(DecisionHistory(
            pereval_id=pereval_id, old_status=pereval.status, new_status=data.status,
            actor_user_id=principal.user_id, actor_role=principal.role.value, reason=data.reason,
        ))
        pereval.status = data.status
        db.flush()
