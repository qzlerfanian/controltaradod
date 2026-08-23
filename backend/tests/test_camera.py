import pytest

from rahband.domain.camera import Camera, CameraDirection


def test_camera_keeps_entry_or_exit_tag() -> None:
    camera = Camera(id="gate-in", name="ورودی اصلی", direction=CameraDirection.ENTRY)
    assert camera.direction is CameraDirection.ENTRY


def test_camera_id_is_required() -> None:
    with pytest.raises(ValueError, match="camera id"):
        Camera(id=" ", name="ورودی", direction=CameraDirection.ENTRY)

