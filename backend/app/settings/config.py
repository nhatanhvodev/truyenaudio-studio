from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDIO_", extra="forbid")

    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: Literal[8765] = 8765
    worker_concurrency: Literal[1] = 1
    data_root: Path = Path(r"D:\truyenaudio-studio\data")
    enable_asr_backcheck: bool = False

    @field_validator("host", mode="before")
    @classmethod
    def reject_non_loopback_host(cls, value: object) -> object:
        if value != "127.0.0.1":
            raise ValueError("host must be loopback")
        return value

    @model_validator(mode="after")
    def enforce_local(self):
        if self.host != "127.0.0.1":
            raise ValueError("host must be loopback")
        return self
