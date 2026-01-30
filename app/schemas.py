from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    fam: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    otc: Optional[str] = None


class CoordsCreate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    height: int = Field(..., gt=0)


class LevelCreate(BaseModel):
    spring: Optional[str] = None
    summer: Optional[str] = None
    autumn: Optional[str] = None
    winter: Optional[str] = None


class ImageCreate(BaseModel):
    img: str
    title: str = Field(..., min_length=1)


class PerevalCreate(BaseModel):
    beauty_title: Optional[str] = None
    title: str = Field(..., min_length=1)
    other_titles: Optional[str] = None
    connect: Optional[str] = None
    add_time: Optional[datetime] = Field(default_factory=datetime.now)
    user: UserCreate
    coords: CoordsCreate
    level: LevelCreate
    images: List[ImageCreate] = []


class SubmitResponse(BaseModel):
    status: int = 200
    message: str = "Отправлено успешно"
    id: int


class ErrorResponse(BaseModel):
    status: int
    message: str
    detail: Optional[str] = None


class PerevalResponse(PerevalCreate):
    id: int
    status: str
    date_added: datetime

    model_config = ConfigDict(from_attributes=True)


class PerevalList(BaseModel):
    id: int
    title: str
    status: str
    date_added: datetime
    user_email: str
    latitude: float
    longitude: float
    height: int

    model_config = ConfigDict(from_attributes=True)


class PerevalUpdate(BaseModel):
    beauty_title: Optional[str] = None
    title: Optional[str] = None
    other_titles: Optional[str] = None
    connect: Optional[str] = None
    add_time: Optional[datetime] = None
    coords: Optional[CoordsCreate] = None
    level: Optional[LevelCreate] = None
    images: Optional[List[ImageCreate]] = None

    model_config = ConfigDict(extra="forbid")


class UpdateResponse(BaseModel):
    state: int
    message: str