from __future__ import annotations

from threading import Lock


class NrcPulseRelay:
    """Short-lived NRC connection; the board owns physical close timing."""

    def __init__(self, host: str, port: int, username: str, password: str, channel: int, contact_ms: int) -> None:
        self._address = (host, port, username, password)
        self._channel = channel
        self._contact_ms = contact_ms
        self._lock = Lock()

    def trigger(self) -> None:
        from nrcpy import NrcDevice

        with self._lock:
            device = NrcDevice(self._address)
            device.connect()
            if not device.login():
                raise ConnectionError("NRC relay login failed")
            device.relayContact(self._channel, self._contact_ms)
            device.relayOn(self._channel)


class DisabledRelay:
    def trigger(self) -> None:
        raise ConnectionError("relay is not configured")

