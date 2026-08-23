from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from rahband.config import CameraSettings, Settings, get_settings
from rahband.domain.events import PassageDirection
from rahband.runtime import AppRuntime


class ServiceHealth(BaseModel):
    status: Literal["healthy", "degraded"]
    database: Literal["connected", "disconnected", "not_configured"]
    mqtt: Literal["connected", "disconnected"]
    relay: Literal["configured", "not_configured"]
    cameras_online: int
    cameras_total: int
    checked_at: datetime


class CameraResponse(BaseModel):
    id: str
    name: str
    ip: str
    direction: Literal["entry", "exit"]
    relay_id: str
    enabled: bool
    online: bool = False


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
        runtime.mqtt.start()
    except Exception:
        pass
    if runtime.camera_supervisor is not None:
        runtime.camera_supervisor.start()
    yield
    if runtime.camera_supervisor is not None:
        runtime.camera_supervisor.stop()
    runtime.mqtt.stop()


app = FastAPI(title="Rahband API", version="0.2.0", lifespan=lifespan)
settings_at_import = get_settings()
app.add_middleware(
    CORSMiddleware, allow_origins=settings_at_import.allowed_origins,
    allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Control-Token"],
)


def runtime() -> AppRuntime:
    return app.state.runtime


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
    mqtt_state = "connected" if current.mqtt.connected else "disconnected"
    healthy = database == "connected" and relay_configured and mqtt_state == "connected"
    cameras = current.settings.cameras
    camera_health = current.camera_health.snapshot()
    online_count = sum(1 for item in camera_health.values() if item["online"])
    return ServiceHealth(
        status="healthy" if healthy else "degraded", database=database, mqtt=mqtt_state,
        relay="configured" if relay_configured else "not_configured", cameras_online=online_count,
        cameras_total=len([camera for camera in cameras if camera.enabled]), checked_at=datetime.now(timezone.utc),
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


@app.get("/api/v1/events")
def events(current: Annotated[AppRuntime, Depends(runtime)], limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    if current.repository is None:
        return []
    try:
        items = current.repository.recent_events(limit)
        for item in items:
            item["plate_image_url"] = f"/api/v1/images/plate/{item['log_id']}"
            item["vehicle_image_url"] = f"/api/v1/images/vehicle/{item['log_id']}"
        return items
    except Exception as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error


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
        return {"id": owner_id, "access": True}
    except Exception as error:
        raise HTTPException(status_code=503, detail="owner could not be saved") from error


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
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)


@app.get("/api/v1/images/{kind}/{log_id}", response_class=FileResponse)
def event_image(kind: Literal["plate", "vehicle"], log_id: str, current: Annotated[AppRuntime, Depends(runtime)]) -> FileResponse:
    from uuid import UUID

    try:
        parsed_id = UUID(log_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="image not found") from error
    path = current.image_store.path_for(kind, parsed_id)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, media_type="image/jpeg")
