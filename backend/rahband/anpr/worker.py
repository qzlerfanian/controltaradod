from __future__ import annotations

import logging
import os
from threading import Event, Thread

from rahband.config import CameraSettings
from rahband.config import RoiSettings
from rahband.domain.events import PassageDirection
from rahband.services.passage import PassageService

from .health import CameraHealthRegistry
from .inference import OpenVinoAnprEngine
from .detection_log import DetectionAuditLog
from .storage import ImageStore
from .voting import TemporalPlateVoter


class CameraWorker(Thread):
    def __init__(
        self,
        camera: CameraSettings,
        engine: OpenVinoAnprEngine,
        passage_service: PassageService,
        image_store: ImageStore,
        health: CameraHealthRegistry,
        detection_log: DetectionAuditLog,
        *,
        vote_window_seconds: float,
        vote_min_observations: int,
        reconnect_seconds: float,
        open_timeout_ms: int = 8000,
        read_timeout_ms: int = 8000,
    ) -> None:
        super().__init__(name=f"camera-{camera.id}", daemon=True)
        self._camera = camera
        self._engine = engine
        self._passage_service = passage_service
        self._image_store = image_store
        self._health = health
        self._detection_log = detection_log
        self._voter = TemporalPlateVoter(vote_window_seconds, vote_min_observations)
        self._reconnect_seconds = reconnect_seconds
        self._open_timeout_ms = open_timeout_ms
        self._read_timeout_ms = read_timeout_ms
        self._stop_event = Event()
        self._logger = logging.getLogger(f"rahband.camera.{camera.id}")

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        import cv2

        while not self._stop_event.is_set():
            capture = cv2.VideoCapture()
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._open_timeout_ms)
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, self._read_timeout_ms)
            capture.open(self._camera.rtsp_url.get_secret_value(), cv2.CAP_FFMPEG)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not capture.isOpened():
                self._health.disconnected(self._camera.id, "RTSP connection failed")
                capture.release()
                self._stop_event.wait(self._reconnect_seconds)
                continue
            self._health.connected(self._camera.id)
            frame_number = 0
            try:
                while not self._stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise ConnectionError("RTSP frame read failed")
                    self._health.frame(self._camera.id)
                    frame_number += 1
                    if frame_number == 1 or frame_number % 15 == 0:
                        preview_frame = frame.copy()
                        if frame.shape[1] > 960:
                            scale = 960 / frame.shape[1]
                            preview_frame = cv2.resize(frame, (960, int(frame.shape[0] * scale)))
                        self._draw_zone(preview_frame, self._camera.trigger_zone)
                        encoded, jpeg = cv2.imencode(".jpg", preview_frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                        if encoded:
                            self._health.preview(self._camera.id, jpeg.tobytes())
                    if frame_number % self._camera.frame_stride:
                        continue
                    for recognition in self._engine.recognize(frame, self._camera.roi):
                        self._detection_log.record("observed", self._camera, recognition)
                        if not self._inside_trigger_zone(recognition, self._camera.trigger_zone):
                            self._detection_log.record("outside_trigger", self._camera, recognition)
                            continue
                        confirmed = self._voter.observe(recognition)
                        if confirmed is None:
                            continue
                        self._detection_log.record("confirmed", self._camera, confirmed)
                        self._logger.info(
                            "plate confirmed=%s confidence=%.3f quality=%.3f sharpness=%.3f size=%sx%s",
                            confirmed.plate.canonical,
                            confirmed.confidence,
                            confirmed.image_quality,
                            confirmed.sharpness,
                            confirmed.plate_width,
                            confirmed.plate_height,
                        )
                        self._health.detection(self._camera.id)
                        try:
                            event = self._passage_service.process_detection(
                                plate_value=confirmed.plate.canonical,
                                direction=PassageDirection(self._camera.direction),
                                camera_ip=self._camera.database_name or self._camera.id[:10],
                                plate_string=confirmed.plate.canonical,
                            )
                        except Exception:
                            # A SQL/MQTT failure must never tear down a healthy RTSP stream.
                            self._logger.exception("detection could not be persisted; camera remains connected")
                            continue
                        if event is not None:
                            try:
                                plate_path, vehicle_path = self._image_store.save(
                                    event,
                                    confirmed.plate_image,
                                    confirmed.vehicle_frame,
                                )
                                self._logger.info(
                                    "event images saved log_id=%s plate=%s vehicle=%s",
                                    event.log_id, plate_path, vehicle_path,
                                )
                            except Exception:
                                self._logger.exception(
                                    "event was committed but its images could not be saved log_id=%s",
                                    event.log_id,
                                )
            except ConnectionError as error:
                self._logger.warning("RTSP interrupted; reconnecting: %s", error)
                self._health.disconnected(self._camera.id, str(error))
            except Exception as error:
                self._logger.exception("unexpected camera worker failure; reconnecting")
                self._health.disconnected(self._camera.id, str(error))
            finally:
                capture.release()
            self._stop_event.wait(self._reconnect_seconds)

    @staticmethod
    def _inside_trigger_zone(recognition: object, zone: RoiSettings | None) -> bool:
        if zone is None:
            return True
        frame_width = int(getattr(recognition, "frame_width", 0))
        frame_height = int(getattr(recognition, "frame_height", 0))
        if frame_width <= 0 or frame_height <= 0:
            return False
        center_x = (int(getattr(recognition, "bbox_x1", 0)) + int(getattr(recognition, "bbox_x2", 0))) / 2
        center_y = (int(getattr(recognition, "bbox_y1", 0)) + int(getattr(recognition, "bbox_y2", 0))) / 2
        x1 = zone.x1 * frame_width / zone.reference_width
        y1 = zone.y1 * frame_height / zone.reference_height
        x2 = zone.x2 * frame_width / zone.reference_width
        y2 = zone.y2 * frame_height / zone.reference_height
        return x1 <= center_x <= x2 and y1 <= center_y <= y2

    @staticmethod
    def _draw_zone(frame: object, zone: RoiSettings | None) -> None:
        if zone is None:
            return
        import cv2

        height, width = frame.shape[:2]
        x1 = round(zone.x1 * width / zone.reference_width)
        y1 = round(zone.y1 * height / zone.reference_height)
        x2 = round(zone.x2 * width / zone.reference_width)
        y2 = round(zone.y2 * height / zone.reference_height)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (40, 220, 80), 3)
        cv2.putText(
            frame, "TRIGGER", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
            0.65, (40, 220, 80), 2, cv2.LINE_AA,
        )


class CameraSupervisor:
    def __init__(self, workers: list[CameraWorker]) -> None:
        self._workers = workers

    def start(self) -> None:
        for worker in self._workers:
            worker.start()

    def stop(self) -> None:
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join(timeout=10)
