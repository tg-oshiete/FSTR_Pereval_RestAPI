"""Public errors never contain exception text, input values or SQL parameters."""

import logging
from typing import get_args

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.schemas import ValidationType
from app.security import AccessDenied


logger = logging.getLogger("app.errors")
ERRORS = {
    "UNAUTHORIZED": (401, "Требуется действительный Bearer-токен"),
    "FORBIDDEN": (403, "Недостаточно прав"),
    "STATUS_CONFLICT": (409, "Текущий статус не совпадает с ожидаемым"),
    "INVALID_STATUS_TRANSITION": (409, "Недопустимый переход статуса"),
    "PEREVAL_NOT_FOUND": (404, "Перевал не найден"),
    "ROUTE_NOT_FOUND": (404, "Маршрут не найден"),
    "METHOD_NOT_ALLOWED": (405, "Метод не поддерживается"),
    "PEREVAL_NOT_EDITABLE": (409, "Редактирование возможно только в статусе new"),
    "USER_PROFILE_CONFLICT": (409, "Данные профиля не совпадают с сохранёнными"),
    "PAYLOAD_TOO_LARGE": (413, "Превышен размер тела запроса"),
    "INTERNAL_ERROR": (500, "Внутренняя ошибка сервера"),
}


def error_response(code: str, *, headers=None) -> JSONResponse:
    status, message = ERRORS[code]
    if code == "UNAUTHORIZED":
        headers = {**(headers or {}), "WWW-Authenticate": "Bearer"}
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}},
                        headers=headers)


def internal_error_response(exc: Exception) -> JSONResponse:
    # Tracebacks, str(exc), URLs and request bodies can expose credentials or PII.
    logger.error("Unexpected server error: %s", type(exc).__name__)
    return error_response("INTERNAL_ERROR")


class APIError(HTTPException):
    def __init__(self, code: str):
        self.code = code
        status, message = ERRORS[code]
        super().__init__(status_code=status, detail={"code": code, "message": message})


MESSAGES = {
    "missing": "Обязательное поле", "extra_forbidden": "Неизвестное или запрещённое поле",
    "string_type": "Нужна строка", "string_too_short": "Строка слишком короткая",
    "string_too_long": "Строка слишком длинная", "string_controls": "Управляющие символы запрещены",
    "int_type": "Нужно целое JSON-число", "float_type": "Нужно JSON-число",
    "finite_number": "Число должно быть конечным", "greater_than_equal": "Значение ниже минимума",
    "less_than_equal": "Значение выше максимума", "list_type": "Нужен массив", "object_type": "Нужен объект",
    "datetime_invalid": "Нужно корректное время RFC 3339 с часовым поясом",
    "email_invalid": "Некорректный email", "level_invalid": "Некорректная категория сложности",
    "empty_patch": "Нужно передать хотя бы одно поле", "empty_object": "Вложенный объект не может быть пустым",
    "image_base64": "Некорректный base64 изображения", "image_bytes": "Превышен размер изображения",
    "image_dimensions": "Недопустимые размеры изображения", "image_pixels": "Превышен лимит пикселей",
    "image_format": "Нужен неповреждённый однокадровый JPEG или PNG",
    "images_count": "Превышено количество фотографий", "images_total_bytes": "Превышен суммарный размер фотографий",
    "json_invalid": "Некорректный JSON", "invalid_value": "Недопустимое значение поля",
}


def validation_response(errors) -> JSONResponse:
    issues = []
    for error in errors:
        loc = list(error.get("loc", ["body"]))
        kind = error["type"]
        if kind == "json_invalid":
            loc = ["body"]
        elif kind in {"model_type", "model_attributes_type", "dict_type"}:
            kind = "object_type"
        elif kind in {"int_parsing", "int_from_float"}:
            kind = "int_type"
        elif kind == "value_error" and loc and loc[-1] in {"email", "user__email"}:
            kind = "email_invalid"
        if kind not in get_args(ValidationType):
            kind = "invalid_value"
        issues.append({"loc": loc, "type": kind, "message": MESSAGES[kind]})
    return JSONResponse(status_code=422, content={"detail": {
        "code": "VALIDATION_ERROR", "message": "Некорректные данные запроса", "errors": issues,
    }})


def install_handlers(application: FastAPI) -> None:
    @application.exception_handler(AccessDenied)
    async def forbidden(request: Request, exc: AccessDenied):
        return error_response("FORBIDDEN")

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return validation_response(exc.errors())

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if isinstance(exc, APIError):
            return error_response(exc.code, headers=exc.headers)
        if exc.status_code in {404, 405}:
            return error_response("ROUTE_NOT_FOUND" if exc.status_code == 404 else "METHOD_NOT_ALLOWED",
                                  headers=exc.headers)
        if exc.status_code == 400:
            return validation_response([{"loc": ["body"], "type": "json_invalid"}])
        return error_response("INTERNAL_ERROR")

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return internal_error_response(exc)
