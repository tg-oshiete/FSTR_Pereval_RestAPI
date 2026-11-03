"""One set of upload limits and strict image validation for both write routes."""

import base64
import binascii
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO
import struct
import warnings

from PIL import Image, UnidentifiedImageError
from pydantic_core import PydanticCustomError


@dataclass(frozen=True)
class UploadLimits:
    max_images_per_pereval: int = 5
    max_image_bytes: int = 5_242_880
    max_images_total_bytes: int = 20_971_520
    max_image_pixels: int = 24_000_000
    max_image_dimension: int = 12_000
    max_request_bytes: int = 33_554_432

    @property
    def max_base64_length(self) -> int:
        return 4 * ((self.max_image_bytes + 2) // 3)

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, name) for name in cls.__dataclass_fields__})


DEFAULT_LIMITS = UploadLimits()
# ASGI tasks isolate requests; AnyIO propagates this context to sync validators/routes.
upload_limits: ContextVar[UploadLimits] = ContextVar("upload_limits", default=DEFAULT_LIMITS)


def check_dimensions(width: int, height: int, limits: UploadLimits) -> None:
    if not (1 <= width <= limits.max_image_dimension and 1 <= height <= limits.max_image_dimension):
        raise PydanticCustomError("image_dimensions", "Недопустимые размеры изображения")
    if width * height > limits.max_image_pixels:
        raise PydanticCustomError("image_pixels", "Превышен лимит пикселей изображения")


def decode_image(value: str) -> bytes:
    limits = upload_limits.get()
    if len(value) > limits.max_base64_length:
        raise PydanticCustomError("image_bytes", "Превышен размер изображения")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise PydanticCustomError("image_base64", "Некорректный base64 изображения") from None
    if not raw or base64.b64encode(raw).decode("ascii") != value:
        raise PydanticCustomError("image_base64", "Некорректный base64 изображения")
    if len(raw) > limits.max_image_bytes:
        raise PydanticCustomError("image_bytes", "Превышен размер изображения")

    # Inspect PNG geometry before invoking a decoder (including APNG parsing).
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        check_dimensions(*struct.unpack(">II", raw[16:24]), limits)
    try:
        with warnings.catch_warnings():
            # Pillow can otherwise recover an invalid APNG as an ordinary PNG.
            # Decoder warnings are validation failures, not successful fallbacks.
            warnings.simplefilter("error")
            with Image.open(BytesIO(raw), formats=("JPEG", "PNG")) as image:
                check_dimensions(image.width, image.height, limits)
                if getattr(image, "n_frames", 1) != 1 or (
                    image.format == "PNG" and image.get_format_mimetype() == "image/apng"
                ):
                    raise PydanticCustomError("image_format", "Нужен однокадровый JPEG или PNG")
                if image.format == "JPEG" and not raw.endswith(b"\xff\xd9"):
                    raise PydanticCustomError("image_format", "Повреждённое изображение")
                if image.format == "PNG" and not raw.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"):
                    raise PydanticCustomError("image_format", "Повреждённое изображение")
                image.verify()
            # verify() checks the container; load() also fully decodes the pixel stream.
            with Image.open(BytesIO(raw), formats=("JPEG", "PNG")) as image:
                image.load()
    except PydanticCustomError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, struct.error,
            Image.DecompressionBombError, Warning):
        raise PydanticCustomError("image_format", "Нужен неповреждённый однокадровый JPEG или PNG") from None
    return raw
