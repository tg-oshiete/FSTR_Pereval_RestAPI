from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, LargeBinary
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    fam = Column(String, nullable=False)
    name = Column(String, nullable=False)
    otc = Column(String)

    perevals = relationship("PerevalAdded", back_populates="user")


class Coords(Base):
    __tablename__ = "coords"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    height = Column(Float, nullable=False)

    perevals = relationship("PerevalAdded", back_populates="coords")


class PerevalAdded(Base):
    __tablename__ = "pereval_added"

    id = Column(Integer, primary_key=True, index=True)
    date_added = Column(DateTime(timezone=True), server_default=func.now())
    beauty_title = Column(String)
    title = Column(String, nullable=False)
    other_titles = Column(String)
    connect = Column(String)
    add_time = Column(DateTime(timezone=True))
    user_id = Column(Integer, ForeignKey("users.id"))
    coord_id = Column(Integer, ForeignKey("coords.id"))
    level_spring = Column(String(2))
    level_summer = Column(String(2))
    level_winter = Column(String(2))
    level_autumn = Column(String(2))
    status = Column(String, nullable=False, default="new", server_default="new")

    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'pending', 'accepted', 'rejected')",
            name="check_status_values"
        ),
    )

    user = relationship("User", back_populates="perevals")
    coords = relationship("Coords", back_populates="perevals")
    images = relationship("Image", secondary="pereval_images", back_populates="perevals")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    date_added = Column(DateTime(timezone=True), server_default=func.now())
    img = Column(LargeBinary, nullable=False)
    title = Column(String)

    perevals = relationship("PerevalAdded", secondary="pereval_images", back_populates="images")


class PerevalImages(Base):
    __tablename__ = "pereval_images"

    id = Column(Integer, primary_key=True, index=True)
    id_pereval = Column(Integer, ForeignKey("pereval_added.id", ondelete="CASCADE"))
    id_image = Column(Integer, ForeignKey("images.id", ondelete="CASCADE"))


class PerevalAreas(Base):
    __tablename__ = "pereval_areas"

    id = Column(Integer, primary_key=True, index=True)
    id_parent = Column(Integer, nullable=False)
    title = Column(String)


class SprActivitiesTypes(Base):
    __tablename__ = "spr_activities_types"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
