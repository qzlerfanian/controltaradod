from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CameraSettings(BaseModel):
    id: str
    name: str
    ip: str
    rtsp_url: SecretStr
    direction: Literal["entry", "exit"]
    relay_id: str = "main-gate"
    enabled: bool = True
    roi: "RoiSettings | None" = None
    frame_stride: int = Field(default=3, ge=1, le=30)
    database_name: str | None = Field(default=None, min_length=1, max_length=10)


class RoiSettings(BaseModel):
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(gt=0)
    y2: int = Field(gt=0)

    def model_post_init(self, __context: object) -> None:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("ROI x2/y2 must be greater than x1/y1")


CameraSettings.model_rebuild()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAHBAND_", extra="ignore")

    site_id: str = "local-site"
    site_name: str = "راهبند"
    database_connection_string: SecretStr | None = None
    mqtt_host: str = "127.0.0.1"
    mqtt_enabled: bool = False
    mqtt_port: int = 1883
    mqtt_topic: str = "beenato/plateB2"
    relay_host: str | None = None
    relay_port: int = 23
    relay_username: str | None = None
    relay_password: SecretStr | None = None
    relay_channel: int = 1
    relay_contact_ms: int = 100
    relay_hold_seconds: int = 15
    duplicate_window_seconds: int = 20
    cameras_json: str = "[]"
    cameras_override_json: str | None = None
    camera_database_names_json: str = "{}"
    cors_origins: str = "http://localhost:3000"
    control_token: SecretStr | None = None
    shadow_mode: bool = True
    camera_workers_enabled: bool = False
    image_root: str = "../runtime/images"
    outbox_path: str = "../runtime/outbox.sqlite3"
    outbox_retry_seconds: float = Field(default=5, ge=1, le=300)
    plate_model_path: str = "../legacy_source/ANPR/best_yolo11n_60_epoch_plate_openvino_model"
    character_model_path: str = "../legacy_source/ANPR/best_s_openvino_model"
    plate_confidence: float = Field(default=0.04, gt=0, le=1)
    character_confidence: float = Field(default=0.2, gt=0, le=1)
    inference_image_size: int = Field(default=256, ge=128, le=1280)
    vote_window_seconds: float = Field(default=1.5, gt=0, le=10)
    vote_min_observations: int = Field(default=2, ge=1, le=10)
    camera_reconnect_seconds: float = Field(default=3, ge=0.2, le=60)
    camera_open_timeout_ms: int = Field(default=8000, ge=1000, le=60000)
    camera_read_timeout_ms: int = Field(default=8000, ge=1000, le=60000)

    @property
    def cameras(self) -> list[CameraSettings]:
        value = self.cameras_override_json or self.cameras_json
        database_names = json.loads(self.camera_database_names_json)
        cameras = [CameraSettings.model_validate(item) for item in json.loads(value)]
        return [
            camera.model_copy(update={"database_name": database_names.get(camera.ip, camera.database_name)})
            for camera in cameras
        ]

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
