from __future__ import annotations

from dataclasses import dataclass
import logging

from rahband.adapters.database import pyodbc_connection_factory
from rahband.adapters.mqtt import MqttPublisher
from rahband.adapters.relay_nrc import DisabledRelay, NrcPulseRelay
from rahband.config import Settings
from rahband.domain.deduplication import PassageDeduplicator
from rahband.domain.relay import RelayCoordinator
from rahband.repositories.sqlserver import SqlServerRepository
from rahband.services.passage import PassageService
from rahband.services.outbox import OutboxReplayer, SqlitePassageOutbox
from rahband.anpr.health import CameraHealthRegistry
from rahband.anpr.storage import ImageStore
from rahband.anpr.worker import CameraSupervisor, CameraWorker


@dataclass(slots=True)
class AppRuntime:
    settings: Settings
    repository: SqlServerRepository | None
    relay: RelayCoordinator
    mqtt: MqttPublisher
    passage_service: PassageService | None
    image_store: ImageStore
    camera_health: CameraHealthRegistry
    camera_supervisor: CameraSupervisor | None
    outbox: SqlitePassageOutbox
    outbox_replayer: OutboxReplayer | None
    camera_startup_error: str | None = None

    @classmethod
    def build(cls, settings: Settings) -> "AppRuntime":
        repository = None
        if settings.database_connection_string is not None:
            repository = SqlServerRepository(pyodbc_connection_factory(settings.database_connection_string.get_secret_value()))

        if settings.relay_host and settings.relay_username and settings.relay_password:
            driver = NrcPulseRelay(
                settings.relay_host, settings.relay_port, settings.relay_username,
                settings.relay_password.get_secret_value(), settings.relay_channel,
                settings.relay_contact_ms,
            )
        else:
            driver = DisabledRelay()
        relay = RelayCoordinator(driver, settings.relay_hold_seconds)
        mqtt = MqttPublisher(settings.mqtt_host, settings.mqtt_port, settings.mqtt_topic, settings.site_id)
        image_store = ImageStore(settings.image_root)
        outbox = SqlitePassageOutbox(settings.outbox_path)
        enabled_cameras = [camera for camera in settings.cameras if camera.enabled]
        camera_health = CameraHealthRegistry([camera.id for camera in enabled_cameras])
        passage_service = None
        outbox_replayer = None
        if repository is not None:
            passage_service = PassageService(
                repository, relay, PassageDeduplicator(settings.duplicate_window_seconds),
                mqtt if settings.mqtt_enabled else None,
                relay_enabled=not settings.shadow_mode,
                outbox=outbox,
            )
            outbox_replayer = OutboxReplayer(outbox, repository, settings.outbox_retry_seconds)
        supervisor = None
        startup_error = None
        if settings.camera_workers_enabled:
            if passage_service is None:
                startup_error = "camera workers require a configured database"
            else:
                try:
                    from rahband.anpr.inference import OpenVinoAnprEngine

                    engine = OpenVinoAnprEngine(
                        settings.plate_model_path, settings.character_model_path,
                        image_size=settings.inference_image_size,
                        plate_confidence=settings.plate_confidence,
                        character_confidence=settings.character_confidence,
                    )
                    workers = [
                        CameraWorker(
                            camera, engine, passage_service, image_store, camera_health,
                            vote_window_seconds=settings.vote_window_seconds,
                            vote_min_observations=settings.vote_min_observations,
                            reconnect_seconds=settings.camera_reconnect_seconds,
                            open_timeout_ms=settings.camera_open_timeout_ms,
                            read_timeout_ms=settings.camera_read_timeout_ms,
                        )
                        for camera in enabled_cameras
                    ]
                    supervisor = CameraSupervisor(workers)
                except Exception as error:
                    logging.getLogger(__name__).exception("camera subsystem could not start")
                    startup_error = str(error)
        return cls(
            settings, repository, relay, mqtt, passage_service, image_store,
            camera_health, supervisor, outbox, outbox_replayer, startup_error,
        )
