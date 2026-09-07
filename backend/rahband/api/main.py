from __future__ import annotations

import asyncio
import csv
import io
import secrets
import socket
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from rahband.config import CameraSettings, Settings, get_settings
from rahband.domain.events import PassageDirection
from rahband.repositories.sqlserver import DuplicateOwnerError
from rahband.runtime import AppRuntime


class ServiceHealth(BaseModel):
    status: Literal["healthy", "degraded"]
    database: Literal["connected", "disconnected", "not_configured"]
    mqtt: Literal["connected", "disconnected", "disabled"]
    relay: Literal["configured", "not_configured"]
    cameras_online: int
    cameras_total: int
    checked_at: datetime
    queued_events: int = 0


class CameraResponse(BaseModel):
    id: str
    name: str
    ip: str
    direction: Literal["entry", "exit"]
    relay_id: str
    enabled: bool
    online: bool = False
    database_name: str | None = None


class DetectionRequest(BaseModel):
    plate_number: str = Field(min_length=8, max_length=8)
    direction: Literal["entry", "exit"]
    camera_ip: str
    plate_string: str | None = None
    occurred_at: datetime | None = None


class RelayResponse(BaseModel):
    triggered: bool
    extended: bool
    deadline: datetime | None


class OwnerCreateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    model: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    two: str = Field(min_length=2, max_length=2)
    three: str = Field(min_length=3, max_length=3)
    city: str = Field(min_length=2, max_length=2)
    alpha: str = Field(min_length=1, max_length=20)
    unit: str | None = None
    access_type: int = 0
    description: str | None = None
    user_id: int | None = None
    access: bool = True


class OwnerAccessRequest(BaseModel):
    access: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in tuple(self._clients):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        async with self._lock:
            for websocket in stale:
                self._clients.discard(websocket)


hub = WebSocketHub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime = AppRuntime.build(settings)
    app.state.runtime = runtime
    try:
        if settings.mqtt_enabled:
            runtime.mqtt.start()
    except Exception:
        pass
    if runtime.camera_supervisor is not None:
        runtime.camera_supervisor.start()
    if runtime.outbox_replayer is not None:
        runtime.outbox_replayer.start()
    runtime.image_retention_worker.start()
    yield
    runtime.image_retention_worker.stop()
    runtime.image_retention_worker.join(timeout=5)
    if runtime.outbox_replayer is not None:
        runtime.outbox_replayer.stop()
        runtime.outbox_replayer.join(timeout=10)
    if runtime.camera_supervisor is not None:
        runtime.camera_supervisor.stop()
    if settings.mqtt_enabled:
        runtime.mqtt.stop()


app = FastAPI(title="Rahband API", version="0.2.0", lifespan=lifespan)
settings_at_import = get_settings()
app.add_middleware(
    CORSMiddleware, allow_origins=settings_at_import.allowed_origins,
    allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Control-Token"],
)

SESSION_COOKIE = "rahband_session"
PUBLIC_API_PATHS = {"/api/v1/auth/login", "/api/v1/health"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS and not path.startswith("/api/v1/internal/"):
        current: AppRuntime = request.app.state.runtime
        user = current.auth.user_for_session(request.cookies.get(SESSION_COOKIE))
        if user is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.user = user
    return await call_next(request)


def runtime() -> AppRuntime:
    return app.state.runtime


@app.post("/api/v1/auth/login")
def login(request: LoginRequest, current: Annotated[AppRuntime, Depends(runtime)]) -> Response:
    authenticated = current.auth.authenticate(request.username, request.password)
    if authenticated is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    token, user = authenticated
    response = JSONResponse({"id": user["id"], "username": user["username"], "expires_at": user["expires_at"]})
    response.set_cookie(
        SESSION_COOKIE, token, max_age=current.settings.auth_session_hours * 3600,
        httponly=True, secure=current.settings.auth_cookie_secure, samesite="strict", path="/",
    )
    return response


@app.get("/api/v1/auth/me")
def authenticated_user(request: Request) -> dict[str, object]:
    return request.state.user


@app.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request, current: Annotated[AppRuntime, Depends(runtime)]) -> Response:
    current.auth.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return response


def require_control_token(
    current: Annotated[AppRuntime, Depends(runtime)],
    x_control_token: Annotated[str | None, Header()] = None,
) -> AppRuntime:
    expected = current.settings.control_token
    if expected is None or x_control_token is None or not secrets.compare_digest(x_control_token, expected.get_secret_value()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid control token")
    return current


@app.get("/api/v1/health", response_model=ServiceHealth)
def health(current: Annotated[AppRuntime, Depends(runtime)]) -> ServiceHealth:
    if current.repository is None:
        database = "not_configured"
    else:
        try:
            database = "connected" if current.repository.ping() else "disconnected"
        except Exception:
            database = "disconnected"
    relay_configured = bool(current.settings.relay_host and current.settings.relay_password)
    mqtt_state = (
        "disabled" if not current.settings.mqtt_enabled
        else "connected" if current.mqtt.connected
        else "disconnected"
    )
    healthy = database == "connected" and relay_configured and (
        not current.settings.mqtt_enabled or mqtt_state == "connected"
    )
    cameras = current.settings.cameras
    camera_health = current.camera_health.snapshot()
    online_count = sum(1 for item in camera_health.values() if item["online"])
    return ServiceHealth(
        status="healthy" if healthy else "degraded", database=database, mqtt=mqtt_state,
        relay="configured" if relay_configured else "not_configured", cameras_online=online_count,
        cameras_total=len([camera for camera in cameras if camera.enabled]), checked_at=datetime.now(timezone.utc),
        queued_events=current.outbox.count(),
    )


@app.get("/api/v1/cameras", response_model=list[CameraResponse])
def cameras(current: Annotated[AppRuntime, Depends(runtime)]) -> list[CameraResponse]:
    health = current.camera_health.snapshot()
    return [
        CameraResponse(
            **camera.model_dump(exclude={"rtsp_url", "roi", "frame_stride"}),
            online=bool(health.get(camera.id, {}).get("online", False)),
        )
        for camera in current.settings.cameras
    ]


@app.get("/api/v1/cameras/health")
def camera_health(current: Annotated[AppRuntime, Depends(runtime)]) -> dict[str, dict[str, object]]:
    return current.camera_health.snapshot()


@app.get("/api/v1/cameras/{camera_id}/snapshot", response_class=Response)
def camera_snapshot(camera_id: str, current: Annotated[AppRuntime, Depends(runtime)]) -> Response:
    if camera_id not in {camera.id for camera in current.settings.cameras if camera.enabled}:
        raise HTTPException(status_code=404, detail="camera not found")
    jpeg = current.camera_health.latest_preview(camera_id)
    if jpeg is None:
        raise HTTPException(status_code=503, detail="camera frame is not ready")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/v1/events")
def events(current: Annotated[AppRuntime, Depends(runtime)], limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    if current.repository is None:
        return []
    try:
        items = current.repository.recent_events(limit)
        for item in items:
            attach_image_urls(current, item)
        return items
    except Exception as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error


def passage_filters(
    date_from: date | None, date_to: date | None, direction: str | None,
    access: str | None, camera: str | None, search: str | None,
) -> dict[str, Any]:
    return {
        "date_from": datetime.combine(date_from, time.min) if date_from else None,
        "date_to": datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None,
        "direction": direction,
        "access": True if access == "authorized" else False if access == "unauthorized" else None,
        "camera": camera.strip() if camera else None,
        "search": search.strip()[:100] if search and search.strip() else None,
    }


def attach_image_urls(current: AppRuntime, item: dict[str, Any]) -> None:
    try:
        log_id = UUID(str(item["log_id"]))
    except (ValueError, TypeError, KeyError):
        item["plate_image_url"] = None
        item["vehicle_image_url"] = None
        return
    item["plate_image_url"] = (
        f"/api/v1/images/plate/{log_id}" if current.image_store.path_for("plate", log_id) else None
    )
    item["vehicle_image_url"] = (
        f"/api/v1/images/vehicle/{log_id}" if current.image_store.path_for("vehicle", log_id) else None
    )


@app.get("/api/v1/passages")
def passages(
    current: Annotated[AppRuntime, Depends(runtime)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 25,
    date_from: date | None = None, date_to: date | None = None,
    direction: Literal["entry", "exit"] | None = None,
    access: Literal["authorized", "unauthorized"] | None = None,
    camera: str | None = None, search: str | None = None,
) -> dict[str, Any]:
    if current.repository is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "pages": 0}
    try:
        items, total = current.repository.paginated_events(
            page=page, page_size=page_size,
            **passage_filters(date_from, date_to, direction, access, camera, search),
        )
        for item in items:
            attach_image_urls(current, item)
        return {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }
    except Exception as error:
        raise HTTPException(status_code=503, detail="passages database unavailable") from error


@app.get("/api/v1/passages/export")
def export_passages(
    current: Annotated[AppRuntime, Depends(runtime)],
    date_from: date | None = None, date_to: date | None = None,
    direction: Literal["entry", "exit"] | None = None,
    access: Literal["authorized", "unauthorized"] | None = None,
    camera: str | None = None, search: str | None = None,
) -> Response:
    if current.repository is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    try:
        items = current.repository.export_events(
            **passage_filters(date_from, date_to, direction, access, camera, search),
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="export database unavailable") from error
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["زمان", "جهت", "پلاک", "مالک", "واحد", "مدل", "رنگ", "دوربین", "دسترسی"])
    for item in items:
        writer.writerow([
            item["trans_time"].isoformat(sep=" "), "ورود" if item["direction"] == "entry" else "خروج",
            item["plate_number"], item["full_name"] or "میهمان", item["unit"] or "",
            item["model"] or "", item["color"] or "", item["camera_ip"] or "",
            "مجاز" if item["access"] else "غیرمجاز",
        ])
    payload = ("\ufeff" + output.getvalue()).encode("utf-8")
    return Response(
        payload, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rahband-passages.csv"'},
    )


@app.get("/api/v1/owners")
def owners(current: Annotated[AppRuntime, Depends(runtime)], limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict[str, Any]]:
    if current.repository is None:
        return []
    try:
        return current.repository.owners(limit)
    except Exception as error:
        raise HTTPException(status_code=503, detail="owners database unavailable") from error


@app.post("/api/v1/owners", status_code=status.HTTP_201_CREATED)
def create_owner(request: OwnerCreateRequest, current: Annotated[AppRuntime, Depends(require_control_token)]) -> dict[str, Any]:
    if current.repository is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    try:
        owner_id = current.repository.create_owner(request.model_dump())
        owner = current.repository.get_owner(owner_id)
        current.owner_audit.record(owner_id, "created", actor="admin", after=owner)
        return owner or {"id": owner_id, "access": request.access}
    except DuplicateOwnerError as error:
        raise HTTPException(status_code=409, detail="plate already exists") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="owner could not be saved") from error


@app.patch("/api/v1/owners/{owner_id}")
def update_owner(owner_id: int, request: OwnerCreateRequest, current: Annotated[AppRuntime, Depends(require_control_token)]) -> dict[str, Any]:
    if current.repository is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    before = current.repository.get_owner(owner_id)
    if before is None:
        raise HTTPException(status_code=404, detail="owner not found")
    try:
        after = current.repository.update_owner(owner_id, request.model_dump())
        current.owner_audit.record(owner_id, "updated", actor="admin", before=before, after=after)
        return after or {}
    except DuplicateOwnerError as error:
        raise HTTPException(status_code=409, detail="plate already exists") from error


@app.patch("/api/v1/owners/{owner_id}/access")
def set_owner_access(owner_id: int, request: OwnerAccessRequest, current: Annotated[AppRuntime, Depends(require_control_token)]) -> dict[str, Any]:
    if current.repository is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    before = current.repository.get_owner(owner_id)
    if before is None:
        raise HTTPException(status_code=404, detail="owner not found")
    after = current.repository.set_owner_access(owner_id, request.access)
    current.owner_audit.record(owner_id, "access_enabled" if request.access else "access_disabled", actor="admin", before=before, after=after)
    return after or {}


@app.delete("/api/v1/owners/{owner_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_owner(owner_id: int, current: Annotated[AppRuntime, Depends(require_control_token)]) -> Response:
    if current.repository is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    before = current.repository.get_owner(owner_id)
    if before is None:
        raise HTTPException(status_code=404, detail="owner not found")
    current.repository.delete_owner(owner_id)
    current.owner_audit.record(owner_id, "deleted", actor="admin", before=before)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/owners/{owner_id}/history")
def owner_history(owner_id: int, current: Annotated[AppRuntime, Depends(runtime)]) -> list[dict[str, Any]]:
    return current.owner_audit.history(owner_id)


@app.get("/api/v1/system/status")
def system_status(current: Annotated[AppRuntime, Depends(runtime)]) -> dict[str, Any]:
    try:
        database = bool(current.repository and current.repository.ping())
    except Exception:
        database = False
    relay_reachable = False
    if current.settings.relay_host:
        try:
            with socket.create_connection((current.settings.relay_host, current.settings.relay_port), timeout=1):
                relay_reachable = True
        except OSError:
            pass
    now = datetime.now(timezone.utc)
    camera_settings = {camera.id: camera for camera in current.settings.cameras}
    camera_rows = []
    for camera_id, health_item in current.camera_health.snapshot().items():
        disconnected = health_item.get("disconnected_since")
        offline_seconds = int((now - disconnected).total_seconds()) if isinstance(disconnected, datetime) else 0
        camera = camera_settings[camera_id]
        camera_rows.append({
            **health_item, "name": camera.name, "ip": camera.ip, "direction": camera.direction,
            "offline_seconds": offline_seconds,
            "long_disconnect": not health_item["online"] and offline_seconds >= current.settings.camera_offline_alert_seconds,
            "snapshot_url": f"/api/v1/cameras/{camera_id}/snapshot",
        })
    return {
        "checked_at": now, "database": database, "relay": relay_reachable,
        "relay_configured": bool(current.settings.relay_host), "cameras": camera_rows,
        "images": current.image_retention.statistics(), "queued_events": current.outbox.count(),
        "camera_startup_error": current.camera_startup_error,
    }


@app.get("/api/v1/stats/today")
def today_stats(current: Annotated[AppRuntime, Depends(runtime)]) -> dict[str, int]:
    if current.repository is None:
        return {"entry": 0, "exit": 0, "total": 0}
    try:
        return current.repository.today_stats()
    except Exception as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error


@app.post("/api/v1/relay/trigger", response_model=RelayResponse)
def trigger_relay(current: Annotated[AppRuntime, Depends(require_control_token)]) -> RelayResponse:
    try:
        result = current.relay.authorize()
        return RelayResponse(triggered=result.triggered, extended=result.extended, deadline=result.deadline)
    except Exception as error:
        raise HTTPException(status_code=503, detail="relay unavailable") from error


@app.post("/api/v1/internal/detections")
async def submit_detection(request: DetectionRequest, current: Annotated[AppRuntime, Depends(require_control_token)]) -> dict[str, Any]:
    if current.passage_service is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    event = await asyncio.to_thread(
        current.passage_service.process_detection, plate_value=request.plate_number,
        direction=PassageDirection(request.direction), camera_ip=request.camera_ip,
        plate_string=request.plate_string, occurred_at=request.occurred_at,
    )
    if event is None:
        return {"accepted": False, "reason": "duplicate"}
    payload = current.passage_service.serialize(event)
    await hub.broadcast({"type": "passage.created", "data": payload})
    return {"accepted": True, "event": payload}


@app.websocket("/api/v1/ws/events")
async def event_stream(websocket: WebSocket) -> None:
    current: AppRuntime = websocket.app.state.runtime
    if current.auth.user_for_session(websocket.cookies.get(SESSION_COOKIE)) is None:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)


@app.get("/api/v1/images/{kind}/{log_id}", response_class=FileResponse)
def event_image(kind: Literal["plate", "vehicle"], log_id: str, current: Annotated[AppRuntime, Depends(runtime)]) -> FileResponse:
    try:
        parsed_id = UUID(log_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="image not found") from error
    path = current.image_store.path_for(kind, parsed_id)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, media_type="image/jpeg")
