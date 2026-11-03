from datetime import datetime, timezone
import re
from typing import Annotated, ClassVar, Literal

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr,
                      Field, PrivateAttr, StringConstraints, field_validator, model_validator)
from pydantic_core import PydanticCustomError

from app.validation import decode_image, upload_limits


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def no_controls(value: str, *, multiline=False) -> str:
    allowed = "\n\t" if multiline else ""
    if any((ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value):
        raise PydanticCustomError("string_controls", "Управляющие символы запрещены")
    return value


def text(max_length, *, multiline=False):
    return Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1,
                                            max_length=max_length),
                     AfterValidator(lambda value: no_controls(value, multiline=multiline))]


Title = text(255)
Name = text(100)
OtherTitles = text(1024)
Connect = text(2000, multiline=True)
Phone = text(32)


def email_input(value):
    if isinstance(value, str):
        return no_controls(value.strip())
    return value


NormalizedEmail = Annotated[EmailStr, BeforeValidator(email_input), Field(max_length=254)]
Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
Height = Annotated[int, Field(strict=True, ge=1, le=10000)]


def utc_timestamp(value):
    if isinstance(value, str):
        # RFC 3339 requires an offset and T; do not accept epochs or naive dates.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
            raise PydanticCustomError("datetime_invalid", "Нужно время RFC 3339 с часовым поясом")
        try:
            value = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        except ValueError:
            raise PydanticCustomError("datetime_invalid", "Некорректное время") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PydanticCustomError("datetime_invalid", "Нужно время RFC 3339 с часовым поясом")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise PydanticCustomError("datetime_invalid", "Время вне допустимого диапазона UTC") from None


UTCTimestamp = Annotated[datetime, BeforeValidator(utc_timestamp)]
Difficulty = Literal["n/k", "1A", "1B", "2A", "2B", "3A", "3B"]
PerevalStatus = Literal["new", "pending", "accepted", "rejected"]


def difficulty(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().upper().translate(str.maketrans({"А": "A", "Б": "B"}))
        if normalized in {"N/K", "Н/К"}:
            return "n/k"
        if normalized in {"1A", "1B", "2A", "2B", "3A", "3B"}:
            return normalized
    raise PydanticCustomError("level_invalid", "Некорректная категория сложности")


DifficultyInput = Annotated[str, Field(
    pattern=r"^\s*(?:[1-3][AaBbАаБб]|[Nn]/[Kk]|[Нн]/[Кк])\s*$",
    description="Категория; краевые пробелы, нижний регистр и кириллические А/Б допустимы",
)]
Season = Annotated[Difficulty | None, BeforeValidator(difficulty, json_schema_input_type=DifficultyInput | None)]


class UserCreate(StrictModel):
    email: NormalizedEmail
    phone: Phone | None = None
    fam: Name
    name: Name
    otc: Name | None = None


class CoordsCreate(StrictModel):
    latitude: Latitude
    longitude: Longitude
    height: Height


class NonEmptyUpdate(StrictModel):
    empty_error: ClassVar[str] = "empty_object"

    @model_validator(mode="before")
    @classmethod
    def require_changes(cls, value):
        if isinstance(value, dict) and not value:
            raise PydanticCustomError(cls.empty_error, "Нужно передать хотя бы одно поле")
        return value


class CoordsUpdate(NonEmptyUpdate):
    # A default marks omission; supplied null is validated by the non-nullable type.
    latitude: Latitude = None
    longitude: Longitude = None
    height: Height = None


class LevelCreate(StrictModel):
    spring: Season = None
    summer: Season = None
    autumn: Season = None
    winter: Season = None


class LevelUpdate(LevelCreate, NonEmptyUpdate):
    pass


class ImageCreate(StrictModel):
    img: str = Field(min_length=1, description="Строгий RFC 4648 base64 однокадрового JPEG/PNG")
    title: Title
    _decoded_bytes: bytes = PrivateAttr()

    @model_validator(mode="after")
    def validate_file(self):
        self._decoded_bytes = decode_image(self.img)
        return self

    @property
    def decoded_bytes(self) -> bytes:
        return self._decoded_bytes


class ImageCollection(StrictModel):
    @field_validator("images", mode="before", check_fields=False)
    @classmethod
    def check_count(cls, value):
        if isinstance(value, list) and len(value) > upload_limits.get().max_images_per_pereval:
            raise PydanticCustomError("images_count", "Превышено количество фотографий")
        return value

    @field_validator("images", check_fields=False)
    @classmethod
    def check_total(cls, value):
        if sum(len(image.decoded_bytes) for image in value) > upload_limits.get().max_images_total_bytes:
            raise PydanticCustomError("images_total_bytes", "Превышен суммарный размер фотографий")
        return value


class PerevalCreate(ImageCollection):
    beauty_title: Title | None = None
    title: Title
    other_titles: OtherTitles | None = None
    connect: Connect | None = None
    add_time: UTCTimestamp = Field(default_factory=lambda: datetime.now(timezone.utc))
    user: UserCreate
    coords: CoordsCreate
    level: LevelCreate
    images: list[ImageCreate] = Field(default_factory=list)


class PerevalUpdate(ImageCollection, NonEmptyUpdate):
    empty_error: ClassVar[str] = "empty_patch"

    beauty_title: Title | None = None
    title: Title = None
    other_titles: OtherTitles | None = None
    connect: Connect | None = None
    add_time: UTCTimestamp = None
    coords: CoordsUpdate = None
    level: LevelUpdate = None
    images: list[ImageCreate] = None


class SubmitResponse(StrictModel):
    status: Literal[201] = 201
    message: Literal["Отправлено успешно"] = "Отправлено успешно"
    id: int = Field(ge=1, le=2147483647)


class UserResponse(UserCreate):
    phone: Phone | None
    otc: Name | None


class LevelResponse(LevelCreate):
    spring: Season
    summer: Season
    autumn: Season
    winter: Season


class PerevalResponse(PerevalCreate):
    beauty_title: Title | None
    other_titles: OtherTitles | None
    connect: Connect | None
    add_time: UTCTimestamp
    user: UserResponse
    level: LevelResponse
    images: list[ImageCreate]
    id: int = Field(ge=1, le=2147483647)
    status: PerevalStatus
    date_added: UTCTimestamp


class PerevalList(StrictModel):
    id: int = Field(ge=1, le=2147483647)
    title: Title
    status: PerevalStatus
    date_added: UTCTimestamp
    user_email: NormalizedEmail
    latitude: Latitude
    longitude: Longitude
    height: Height


class UpdateResponse(StrictModel):
    state: Literal[1] = 1
    message: Literal["Обновлено"] = "Обновлено"


class StatusUpdate(StrictModel):
    expected_status: PerevalStatus
    status: PerevalStatus
    reason: text(1000, multiline=True) | None = None

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.status == "rejected" and self.reason is None:
            raise PydanticCustomError("invalid_value", "Для отклонения обязательна причина")
        return self


class StatusResponse(StrictModel):
    id: int = Field(ge=1, le=2147483647)
    status: PerevalStatus


class DecisionActor(StrictModel):
    user_id: int = Field(ge=1, le=2147483647)
    role: Literal["moderator"]


class HistoryResponse(StrictModel):
    id: int = Field(ge=1, le=2147483647)
    pereval_id: int = Field(ge=1, le=2147483647)
    old_status: PerevalStatus
    new_status: PerevalStatus
    actor: DecisionActor
    changed_at: UTCTimestamp
    reason: text(1000, multiline=True) | None


ErrorCode = Literal["PEREVAL_NOT_FOUND", "ROUTE_NOT_FOUND", "METHOD_NOT_ALLOWED",
                    "PEREVAL_NOT_EDITABLE", "USER_PROFILE_CONFLICT", "PAYLOAD_TOO_LARGE", "INTERNAL_ERROR",
                    "UNAUTHORIZED", "FORBIDDEN", "STATUS_CONFLICT", "INVALID_STATUS_TRANSITION"]
ValidationType = Literal[
    "missing", "extra_forbidden", "string_type", "string_too_short", "string_too_long", "string_controls",
    "int_type", "float_type", "finite_number", "greater_than_equal", "less_than_equal", "list_type",
    "object_type", "datetime_invalid", "email_invalid", "level_invalid", "empty_patch", "empty_object",
    "image_base64", "image_bytes", "image_dimensions", "image_pixels", "image_format", "images_count",
    "images_total_bytes", "json_invalid", "invalid_value",
]


class ErrorDetail(StrictModel):
    code: ErrorCode
    message: str


class ErrorResponse(StrictModel):
    detail: ErrorDetail


class ValidationIssue(StrictModel):
    loc: list[str | int]
    type: ValidationType
    message: str


class ValidationErrorDetail(StrictModel):
    code: Literal["VALIDATION_ERROR"] = "VALIDATION_ERROR"
    message: Literal["Некорректные данные запроса"] = "Некорректные данные запроса"
    errors: list[ValidationIssue] = Field(min_length=1)


class ValidationErrorResponse(StrictModel):
    detail: ValidationErrorDetail
