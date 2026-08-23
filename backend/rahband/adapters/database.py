from __future__ import annotations

from collections.abc import Callable


def pyodbc_connection_factory(connection_string: str, *, timeout: int = 3) -> Callable[[], object]:
    # Driver 18 provides the current TLS stack on both Windows and Linux.
    # Keep credentials in .env and normalize only transport-related options here.
    normalized = connection_string.replace(
        "{ODBC Driver 17 for SQL Server}", "{ODBC Driver 18 for SQL Server}"
    ).rstrip(";")
    lowered = normalized.lower()
    if "encrypt=" not in lowered:
        normalized += ";Encrypt=yes"
    if "trustservercertificate=" not in lowered:
        normalized += ";TrustServerCertificate=yes"

    def connect() -> object:
        import pyodbc

        return pyodbc.connect(normalized, timeout=timeout)

    return connect
