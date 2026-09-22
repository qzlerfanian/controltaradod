from pydantic import SecretStr

from rahband.anpr.detection_log import DetectionAuditLog
from rahband.anpr.inference import Recognition
from rahband.config import CameraSettings
from rahband.domain.plate import Plate


def test_detection_log_records_observed_plate_and_camera(tmp_path) -> None:
    path = tmp_path / "detections.log"
    audit = DetectionAuditLog(path)
    camera = CameraSettings(
        id="exit-1",
        name="Exit",
        ip="172.43.3.119",
        rtsp_url=SecretStr("rtsp://example"),
        direction="exit",
        database_name="Out",
    )
    recognition = Recognition(
        Plate.from_detection("52x54674"),
        0.91,
        None,
        None,
        character_confidences=(0.9,) * 8,
        image_quality=0.8,
        sharpness=120.5,
        plate_width=180,
        plate_height=60,
    )

    audit.record("observed", camera, recognition)

    content = path.read_text(encoding="utf-8")
    assert "stage=observed" in content
    assert "camera_id=exit-1" in content
    assert "database_camera=Out" in content
    assert "direction=exit" in content
    assert "plate=52x54674" in content


def test_detection_log_rotates_at_configured_size(tmp_path) -> None:
    path = tmp_path / "detections.log"
    path.write_text("x" * 100, encoding="utf-8")
    audit = DetectionAuditLog(path, max_bytes=100, backup_count=2)
    camera = CameraSettings(
        id="entry-1",
        name="Entry",
        ip="172.43.3.120",
        rtsp_url=SecretStr("rtsp://example"),
        direction="entry",
    )
    recognition = Recognition(Plate.from_detection("52x54674"), 0.9, None, None)

    audit.record("confirmed", camera, recognition)

    assert path.exists()
    assert path.with_name("detections.log.1").exists()
