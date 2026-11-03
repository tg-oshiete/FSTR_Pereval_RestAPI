"""Validated settings; importing this module performs no I/O."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from app.validation import DEFAULT_LIMITS


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FSTR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    db_host: NonEmptyString
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: NonEmptyString
    db_login: NonEmptyString
    db_pass: SecretStr = Field(min_length=1, repr=False)
    port: int = Field(default=8000, validation_alias="PORT", ge=1, le=65535)

    max_images_per_pereval: int = Field(default=DEFAULT_LIMITS.max_images_per_pereval, ge=0)
    max_image_bytes: int = Field(default=DEFAULT_LIMITS.max_image_bytes, ge=1)
    max_images_total_bytes: int = Field(default=DEFAULT_LIMITS.max_images_total_bytes, ge=1)
    max_image_pixels: int = Field(default=DEFAULT_LIMITS.max_image_pixels, ge=1)
    max_image_dimension: int = Field(default=DEFAULT_LIMITS.max_image_dimension, ge=1)
    max_request_bytes: int = Field(default=DEFAULT_LIMITS.max_request_bytes, ge=1)

    @property
    def database_url(self) -> URL:
        # Pass the URL object directly: passwords are never parsed as URL syntax.
        return URL.create(
            "postgresql+psycopg2",
            username=self.db_login,
            password=self.db_pass.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
