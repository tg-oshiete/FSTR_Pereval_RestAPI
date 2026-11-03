from sqlalchemy import (Column, Integer, String, Float, DateTime, ForeignKey,
                        CheckConstraint, LargeBinary, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


def text_check(field, maximum):
    return CheckConstraint(
        f"length(trim({field})) >= 1 AND length({field}) <= {maximum}",
        name=f"ck_{field}_length",
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(254), unique=True, nullable=False)
    phone = Column(String(32))
    fam = Column(String(100), nullable=False)
    name = Column(String(100), nullable=False)
    otc = Column(String(100))

    __table_args__ = tuple(text_check(field, maximum) for field, maximum in (
        ("email", 254), ("phone", 32), ("fam", 100), ("name", 100), ("otc", 100),
    ))

    perevals = relationship("PerevalAdded", back_populates="user")


class Coords(Base):
    __tablename__ = "coords"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    height = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_longitude_range"),
        CheckConstraint("height BETWEEN 1 AND 10000", name="ck_height_range"),
    )

    perevals = relationship("PerevalAdded", back_populates="coords")


class PerevalAdded(Base):
    __tablename__ = "pereval_added"

    id = Column(Integer, primary_key=True, index=True)
    date_added = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    beauty_title = Column(String(255))
    title = Column(String(255), nullable=False)
    other_titles = Column(String(1024))
    connect = Column(String(2000))
    add_time = Column(DateTime(timezone=True), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coord_id = Column(Integer, ForeignKey("coords.id"), nullable=False, unique=True)
    level_spring = Column(String(3))
    level_summer = Column(String(3))
    level_winter = Column(String(3))
    level_autumn = Column(String(3))
    status = Column(String(8), nullable=False, default="new", server_default="new")

    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'pending', 'accepted', 'rejected')",
            name="check_status_values"
        ),
        Index("ix_pereval_added_user_id_id", "user_id", "id"),
    ) + tuple(text_check(field, maximum) for field, maximum in (
        ("title", 255), ("beauty_title", 255), ("other_titles", 1024), ("connect", 2000),
    )) + tuple(CheckConstraint(
        f"level_{season} IN ('n/k', '1A', '1B', '2A', '2B', '3A', '3B')",
        name=f"ck_level_{season}_values",
    ) for season in ("spring", "summer", "winter", "autumn"))

    user = relationship("User", back_populates="perevals")
    coords = relationship("Coords", back_populates="perevals")
    images = relationship("Image", secondary="pereval_images", back_populates="perevals")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    date_added = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    img = Column(LargeBinary, nullable=False)
    title = Column(String(255), nullable=False)

    __table_args__ = (text_check("title", 255),
                      CheckConstraint("length(img) > 0", name="ck_img_nonempty"))

    perevals = relationship("PerevalAdded", secondary="pereval_images", back_populates="images")


class PerevalImages(Base):
    __tablename__ = "pereval_images"

    id = Column(Integer, primary_key=True, index=True)
    id_pereval = Column(Integer, ForeignKey("pereval_added.id", ondelete="CASCADE"), nullable=False)
    id_image = Column(Integer, ForeignKey("images.id", ondelete="CASCADE"), nullable=False, unique=True)
    position = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("id_pereval", "id_image", name="uq_pereval_image"),
        UniqueConstraint("id_pereval", "position", name="uq_pereval_position"),
        CheckConstraint("position >= 0", name="ck_image_position"),
    )


class PerevalAreas(Base):
    __tablename__ = "pereval_areas"

    id = Column(Integer, primary_key=True, index=True)
    id_parent = Column(Integer, nullable=False)
    title = Column(String)


class SprActivitiesTypes(Base):
    __tablename__ = "spr_activities_types"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
