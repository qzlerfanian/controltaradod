from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


class MqttPublisher:
    def __init__(self, host: str, port: int, topic: str, site_id: str) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._site_id = site_id
        self._client: Any = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected())

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, f"rahband-{self._site_id}-{uuid4().hex[:8]}")
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def publish(self, payload: dict[str, Any]) -> None:
        if self._client is None:
            raise ConnectionError("MQTT client is not started")
        info = self._client.publish(self._topic, json.dumps(payload, ensure_ascii=False), qos=1)
        if info.rc != 0:
            raise ConnectionError(f"MQTT publish failed with rc={info.rc}")

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()

