from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from typing import Any, Protocol

from rahband.domain.events import OwnerAccess, PassageEvent
from rahband.domain.plate import Plate


class Cursor(Protocol):
    def execute(self, sql: str, *params: Any) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


class SqlServerRepository:
    """All SQL used against the existing IMPDb schema lives here."""

    OWNER_BY_PLATE = """
SELECT TOP (1)
    [Id], [FullName], [Model], [Color], [Access], [Unit], [Access_Type]
FROM [IMPDb].[CTV].[tbCars]
WHERE [Two] = ? AND [Three] = ? AND [City] = ? AND [Alpha] = ?;
"""

    INSERT_EVENT = """
INSERT INTO [IMPDb].[TEL].[tbPlateLogs]
    ([Id], [Model], [Color], [Fullname], [TransTime], [IO], [CarId], [UserId],
     [Two], [Three], [City], [Alpha], [LogId], [PlateNumber], [PlateString], [CameraIP])
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

    RECENT_EVENTS = """
WITH RecentLogs AS (
    SELECT TOP (?) *
    FROM [IMPDb].[TEL].[tbPlateLogs]
    ORDER BY [TransTime] DESC
)
SELECT
    logs.[Id], COALESCE(cars.[Model], logs.[Model]), COALESCE(cars.[Color], logs.[Color]),
    COALESCE(cars.[FullName], logs.[Fullname]), logs.[TransTime],
    logs.[IO], logs.[CarId], logs.[Two], logs.[Three], logs.[City], logs.[Alpha],
    logs.[LogId], logs.[PlateNumber], logs.[PlateString], logs.[CameraIP],
    COALESCE(cars.[Access], 0), cars.[Unit]
FROM RecentLogs AS logs
OUTER APPLY (
    SELECT TOP (1) car.[Id], car.[FullName], car.[Model], car.[Color], car.[Access], car.[Unit]
    FROM [IMPDb].[CTV].[tbCars] AS car
    WHERE car.[Id] = logs.[CarId]
       OR (car.[Two] = logs.[Two] AND car.[Three] = logs.[Three]
           AND car.[City] = logs.[City] AND car.[Alpha] = logs.[Alpha])
    ORDER BY CASE WHEN car.[Id] = logs.[CarId] THEN 0 ELSE 1 END, car.[Id] DESC
) AS cars
ORDER BY logs.[TransTime] DESC;
"""

    TODAY_STATS = """
SELECT
    SUM(CASE WHEN [IO] = N'ورود' THEN 1 ELSE 0 END) AS [EntryCount],
    SUM(CASE WHEN [IO] = N'خروج' THEN 1 ELSE 0 END) AS [ExitCount],
    COUNT_BIG(*) AS [TotalCount]
FROM [IMPDb].[TEL].[tbPlateLogs]
WHERE [TransTime] >= CONVERT(date, CURRENT_TIMESTAMP)
  AND [TransTime] < DATEADD(day, 1, CONVERT(date, CURRENT_TIMESTAMP));
"""

    OWNERS = """
SELECT TOP (?)
    [Id], [FullName], [Model], [Color], [Two], [Three], [City], [Alpha],
    [Access], [Unit], [Access_Type], [description], [Time], [User_id]
FROM [IMPDb].[CTV].[tbCars]
ORDER BY [Id] DESC;
"""

    INSERT_OWNER = """
INSERT INTO [IMPDb].[CTV].[tbCars]
    ([FullName], [Model], [Color], [Two], [Three], [City], [Alpha], [Access],
     [Unit], [Access_Type], [description], [Time], [User_id])
OUTPUT INSERTED.[Id]
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?);
"""

    DELETE_OWNER_BY_PLATE = """
DELETE FROM [IMPDb].[CTV].[tbCars]
WHERE [Two] = ? AND [Three] = ? AND [City] = ? AND [Alpha] = ?;
"""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def find_owner(self, plate: Plate) -> OwnerAccess | None:
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(self.OWNER_BY_PLATE, plate.two, plate.three, plate.city, plate.persian_alpha)
            row = cursor.fetchone()
        if row is None:
            return None
        return OwnerAccess(
            car_id=int(row[0]), full_name=str(row[1]), model=row[2], color=row[3],
            access=bool(row[4]), unit=row[5], access_type=row[6],
        )

    def insert_event(self, event: PassageEvent) -> None:
        owner = event.owner
        def bounded(value: Any, size: int) -> Any:
            return value[:size] if isinstance(value, str) else value

        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.INSERT_EVENT,
                str(event.id), bounded(owner.model, 100) if owner else None,
                bounded(owner.color, 100) if owner else None,
                bounded(owner.full_name, 200) if owner else None, event.occurred_at,
                "ورود" if event.direction.value == "entry" else "خروج",
                owner.car_id if owner else None, None, event.plate.two, event.plate.three,
                bounded(event.plate.city, 10), bounded(event.plate.persian_alpha, 10), str(event.log_id),
                bounded(event.plate.canonical, 50), bounded(event.plate_string, 50),
                bounded(event.camera_ip, 10),
            )
            connection.commit()

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(self.RECENT_EVENTS, safe_limit)
            rows = cursor.fetchall()
        return [
            {
                "id": str(row[0]), "model": row[1], "color": row[2], "full_name": row[3],
                "trans_time": row[4], "direction": "entry" if row[5] == "ورود" else "exit",
                "car_id": row[6], "two": str(row[7] or ""), "three": str(row[8] or ""),
                "city": str(row[9] or ""), "alpha": row[10], "log_id": str(row[11]),
                "plate_number": row[12], "plate_string": row[13], "camera_ip": row[14],
                "access": bool(row[15]), "unit": row[16],
            }
            for row in rows
        ]

    def today_stats(self) -> dict[str, int]:
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(self.TODAY_STATS)
            row = cursor.fetchone()
        return {"entry": int(row[0] or 0), "exit": int(row[1] or 0), "total": int(row[2] or 0)}

    def owners(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 1000))
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(self.OWNERS, safe_limit)
            rows = cursor.fetchall()
        return [
            {
                "id": row[0], "full_name": row[1], "model": row[2], "color": row[3],
                "two": str(row[4] or ""), "three": str(row[5] or ""),
                "city": str(row[6] or ""), "alpha": row[7], "access": bool(row[8]),
                "unit": row[9], "access_type": row[10], "description": row[11],
                "created_at": row[12], "user_id": row[13],
            }
            for row in rows
        ]

    def create_owner(self, owner: dict[str, Any]) -> int:
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.DELETE_OWNER_BY_PLATE,
                owner["two"], owner["three"], owner["city"], owner["alpha"],
            )
            cursor.execute(
                self.INSERT_OWNER, owner["full_name"], owner.get("model"), owner.get("color"),
                owner["two"], owner["three"], owner["city"], owner["alpha"],
                bool(owner.get("access", True)), owner.get("unit"),
                owner.get("access_type", 0), owner.get("description"),
                owner.get("user_id"),
            )
            row = cursor.fetchone()
            connection.commit()
        return int(row[0])

    def ping(self) -> bool:
        with closing(self._connection_factory()) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1;")
            return cursor.fetchone()[0] == 1
