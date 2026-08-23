from __future__ import annotations

import logging
from threading import Event, Thread

from rahband.config import CameraSettings
from rahband.domain.events import PassageDirection
from rahband.services.passage import PassageService

from .health import CameraHealthRegistry
from .inference import OpenVinoAnprEngine
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
        *,
        vote_window_seconds: float,
        vote_min_observations: int,
        reconnect_seconds: float,
    ) -> None:
        super().__init__(name=f"camera-{camera.id}", daemon=True)
        self._camera = camera
        self._engine = engine
        self._passage_service = passage_service
        self._image_store = image_store
        self._health = health
        self._voter = TemporalPlateVoter(vote_window_seconds, vote_min_observations)
        self._reconnect_seconds = reconnect_seconds
        self._stop_event = Event()
        self._logger = logging.getLogger(f"rahband.camera.{camera.id}")

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        import cv2

        while not self._stop_event.is_set():
            capture = cv2.VideoCapture(self._camera.rtsp_url.get_secret_value(), cv2.CAP_FFMPEG)
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
                    if frame_number % self._camera.frame_stride:
                        continue
                    for recognition in self._engine.recognize(frame, self._camera.roi):
                        confirmed = self._voter.observe(recognition)
                        if confirmed is None:
                            continue
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
                                self._image_store.save(event.log_id, confirmed.plate_image, confirmed.vehicle_frame)
                            except Exception:
                                self._logger.exception("event was committed but its images could not be saved")
            except Exception as error:
                self._logger.exception("camera worker failed; reconnecting")
                self._health.disconnected(self._camera.id, str(error))
            finally:
                capture.release()
            self._stop_event.wait(self._reconnect_seconds)


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
