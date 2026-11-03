from contextlib import asynccontextmanager
import base64
from datetime import timezone
import re
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.openapi.utils import get_openapi
from pydantic import BeforeValidator, Field
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from app.config import Settings, get_settings
from app import crud, services
from app.database import build_engine, get_db
from app.errors import APIError, install_handlers
from app.middleware import RequestPolicyMiddleware
from app.schemas import (ErrorResponse, NormalizedEmail, PerevalCreate, PerevalList,
                         PerevalResponse, PerevalUpdate, SubmitResponse, UpdateResponse,
                         ValidationErrorResponse)
from app.validation import DEFAULT_LIMITS, UploadLimits


API_DESCRIPTION = """
REST API для заявок о горных перевалах. Создание, чтение, частичное изменение
и фильтрация по email. Содержимое редактируется только в статусе new.
Статусы: new, pending, accepted, rejected. Авторизация и модераторские маршруты
запланированы на фазу 4. Правила полей и ошибок: docs/API_CONTRACT.md.
"""


def public_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise PydanticCustomError("int_type", "Нужен целый положительный ID")
    # Bound the textual size as well; arbitrarily large ints are unnecessary.
    if len(value) > 10:
        raise PydanticCustomError("less_than_equal", "ID слишком велик")
    return int(value)


PublicID = Annotated[int, BeforeValidator(public_id), Field(ge=1, le=2147483647)]
router = APIRouter(responses={
    404: {"model": ErrorResponse, "description": "Заявка или маршрут не найдены"},
    405: {"model": ErrorResponse, "description": "Метод не поддерживается; заголовок Allow"},
    413: {"model": ErrorResponse, "description": "Превышен лимит тела запроса"},
    422: {"model": ValidationErrorResponse, "description": "Некорректные данные запроса"},
    500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
})


@router.post("/submitData/", response_model=SubmitResponse, status_code=201,
             summary="Предложить новый перевал",
             responses={409: {"model": ErrorResponse, "description": "Конфликт данных профиля"}})
def submit_data(pereval: PerevalCreate, db: Session = Depends(get_db)):
    try:
        pereval_id = services.create_pereval(db, pereval)
    except services.UserProfileConflict:
        raise APIError("USER_PROFILE_CONFLICT") from None
    return SubmitResponse(id=pereval_id)


def response_time(db: Session, value):
    # SQLite loses offsets; only the fast test adapter restores normalized UTC.
    if value.tzinfo is None and db.get_bind().dialect.name == "sqlite":
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("/submitData/{pereval_id}", response_model=PerevalResponse,
            summary="Получить перевал по ID")
def get_detail_data(pereval_id: PublicID, db: Session = Depends(get_db)):
    pereval = crud.get_pereval(db, pereval_id)
    if pereval is None:
        raise APIError("PEREVAL_NOT_FOUND")
    return PerevalResponse(
        id=pereval.id, status=pereval.status, date_added=response_time(db, pereval.date_added),
        title=pereval.title, beauty_title=pereval.beauty_title,
        other_titles=pereval.other_titles, connect=pereval.connect,
        add_time=response_time(db, pereval.add_time),
        user={field: getattr(pereval.user, field) for field in ("email", "phone", "fam", "name", "otc")},
        coords={field: getattr(pereval.coords, field) for field in ("latitude", "longitude", "height")},
        level={season: getattr(pereval, f"level_{season}") for season in ("spring", "summer", "autumn", "winter")},
        images=[{"title": image.title, "img": base64.b64encode(image.img).decode("ascii")}
                for image in crud.get_images(db, pereval_id)],
    )


@router.patch("/submitData/{pereval_id}", response_model=UpdateResponse,
              summary="Частично изменить перевал в статусе new",
              responses={409: {"model": ErrorResponse, "description": "Заявка уже вышла из статуса new"}})
def update_data(pereval_id: PublicID, update_data: PerevalUpdate, db: Session = Depends(get_db)):
    """Пропуск сохраняет поле; null очищает только необязательные строки и сезоны.

    Координаты и сезоны изменяются по отдельности. images полностью заменяет
    фотографии, [] очищает их. user и неизвестные поля запрещены на любом уровне.
    Пустые PATCH/coords/level запрещены. Ошибка целиком отклоняет изменение.
    """
    try:
        services.update_pereval(db, pereval_id, update_data)
    except services.PerevalNotFound:
        raise APIError("PEREVAL_NOT_FOUND") from None
    except services.PerevalNotEditable:
        raise APIError("PEREVAL_NOT_EDITABLE") from None
    return UpdateResponse()


@router.get("/submitData/", response_model=list[PerevalList], summary="Получить перевалы по email")
def get_email_data(
    user__email: Annotated[NormalizedEmail, Query()],
    limit: Annotated[int, Query(ge=1, le=100, description="Число заявок, 1–100")] = 20,
    offset: Annotated[int, Query(ge=0, le=2147483647, description="Пропустить N заявок")] = 0,
    db: Session = Depends(get_db),
):
    """Массив в порядке id ASC. По умолчанию limit=20, offset=0; фото не загружаются."""
    return [PerevalList(**{**row, "date_added": response_time(db, row["date_added"])})
            for row in crud.get_perevals_by_email(db, user__email, limit=limit, offset=offset)]


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness only; no database queries or configuration disclosure."""
    return {"status": "ok"}


def install_openapi(application: FastAPI) -> None:
    def openapi():
        if application.openapi_schema is not None:
            return application.openapi_schema
        settings = getattr(application.state, "settings", None)
        limits = UploadLimits.from_settings(settings) if settings is not None else DEFAULT_LIMITS
        schema = get_openapi(title=application.title, version=application.version,
                             description=application.description, routes=application.routes)
        schema["info"]["x-upload-limits"] = {
            name.upper(): getattr(limits, name) for name in limits.__dataclass_fields__
        }
        for name, model in schema["components"]["schemas"].items():
            properties = model.get("properties", {})
            if name.startswith("ImageCreate"):
                properties["img"]["maxLength"] = limits.max_base64_length
                properties["img"]["description"] += (
                    f"; файл ≤ {limits.max_image_bytes} байт; сторона ≤ {limits.max_image_dimension}; "
                    f"пикселей ≤ {limits.max_image_pixels}"
                )
            if "images" in properties:
                properties["images"]["maxItems"] = limits.max_images_per_pereval
                properties["images"]["description"] = f"Суммарно ≤ {limits.max_images_total_bytes} байт"
            if name in {"PerevalUpdate", "CoordsUpdate", "LevelUpdate"}:
                model["minProperties"] = 1
                for field in properties.values():
                    # The internal omission sentinel is not an accepted JSON null.
                    if field.get("default", ...) is None and "anyOf" not in field:
                        field.pop("default")
        application.openapi_schema = schema
        return schema

    application.openapi = openapi


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = settings or get_settings()
        application.openapi_schema = None
        engine = None
        try:
            # Tests register their database dependency before entering lifespan.
            if get_db not in application.dependency_overrides:
                engine = build_engine(application.state.settings)
                application.state.session_factory = sessionmaker(
                    bind=engine, autocommit=False, autoflush=False,
                )
            yield
        finally:
            if engine is not None:
                engine.dispose()

    application = FastAPI(title="FSTR Pereval API", version="1.0",
                          description=API_DESCRIPTION, lifespan=lifespan)
    if settings is not None:
        application.state.settings = settings
    application.add_middleware(RequestPolicyMiddleware)
    install_handlers(application)
    application.include_router(router)
    install_openapi(application)
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=get_settings().port, reload=False)
