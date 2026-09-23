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
    trigger_zone: "RoiSettings | None" = None
    frame_stride: int = Field(default=3, ge=1, le=30)
    database_name: str | None = Field(default=None, min_length=1, max_length=10)


class RoiSettings(BaseModel):
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(gt=0)
    y2: int = Field(gt=0)
    reference_width: int = Field(default=1920, gt=0)
    reference_height: int = Field(default=1080, gt=0)

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
    duplicate_window_seconds: int = 60
    cameras_json: str = "[]"
    cameras_override_json: str | None = None
    camera_database_names_json: str = "{}"
    camera_rois_json: str = "{}"
    camera_trigger_zones_json: str = "{}"
    cors_origins: str = "http://localhost:3000"
    control_token: SecretStr | None = None
    shadow_mode: bool = True
    camera_workers_enabled: bool = False
    image_root: str = "../runtime/images"
    image_index_path: str | None = None
    image_match_window_seconds: float = Field(default=15, ge=1, le=300)
    database_utc_offset_minutes: int = Field(default=210, ge=-720, le=840)
    detection_log_enabled: bool = True
    detection_log_path: str = "../runtime/detections.log"
    detection_log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    detection_log_backup_count: int = Field(default=5, ge=0, le=100)
    outbox_path: str = "../runtime/outbox.sqlite3"
    owner_audit_path: str = "../runtime/owner_audit.sqlite3"
    auth_database_path: str = "../runtime/auth.sqlite3"
    auth_session_hours: int = Field(default=12, ge=1, le=720)
    auth_cookie_secure: bool = False
    image_retention_days: int = Field(default=60, ge=1, le=3650)
    image_max_disk_percent: float = Field(default=85, ge=10, le=99)
    image_cleanup_interval_seconds: float = Field(default=3600, ge=60, le=86400)
    camera_offline_alert_seconds: int = Field(default=60, ge=10, le=86400)
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
        roi_values = json.loads(self.camera_rois_json)
        trigger_values = json.loads(self.camera_trigger_zones_json)
        cameras = [CameraSettings.model_validate(item) for item in json.loads(value)]
        return [
            camera.model_copy(update={
                "database_name": database_names.get(camera.ip, camera.database_name),
                "roi": (
                    RoiSettings.model_validate(roi_values[camera.ip])
                    if camera.ip in roi_values else camera.roi
                ),
                "trigger_zone": (
                    RoiSettings.model_validate(trigger_values[camera.ip])
                    if camera.ip in trigger_values else camera.trigger_zone
                ),
            })
            for camera in cameras
        ]

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
