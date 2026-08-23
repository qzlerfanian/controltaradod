from __future__ import annotations

import socket
from pathlib import Path

from rahband.config import get_settings


def tcp(label: str, host: str | None, port: int) -> bool:
    if not host:
        print(f"[SKIP] {label}: not configured")
        return False
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"[OK]   {label}: {host}:{port}")
            return True
    except OSError as error:
        print(f"[FAIL] {label}: {host}:{port} ({error})")
        return False


def database_host(connection_string: str | None) -> str | None:
    if not connection_string:
        return None
    for part in connection_string.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() in {"SERVER", "DATA SOURCE"}:
            return value.strip().split(",")[0]
    return None


def main() -> int:
    settings = get_settings()
    failures = 0
    for label, path in (
        ("plate model", settings.plate_model_path),
        ("character model", settings.character_model_path),
    ):
        if Path(path).resolve().exists():
            print(f"[OK]   {label}")
        else:
            print(f"[FAIL] {label}: path does not exist")
            failures += 1
    db_value = settings.database_connection_string.get_secret_value() if settings.database_connection_string else None
    failures += not tcp("SQL Server", database_host(db_value), 1433)
    failures += not tcp("MQTT", settings.mqtt_host, settings.mqtt_port)
    failures += not tcp("NRC relay", settings.relay_host, settings.relay_port)
    for camera in settings.cameras:
        failures += not tcp(f"camera {camera.id}", camera.ip, 554)
    print(f"\nMode: {'SHADOW (relay disabled)' if settings.shadow_mode else 'LIVE'}")
    print(f"Image root: {Path(settings.image_root).resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

