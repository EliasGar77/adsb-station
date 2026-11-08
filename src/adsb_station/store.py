"""SQLite storage for aircraft and their position history.

Phase 0 uses SQLite on purpose: zero installation, one file, and plenty for a single
receiver (a busy feed is ~10-50 position rows per second). It will move to PostgreSQL +
PostGIS when spatial queries and concurrent readers (the API) arrive. All SQL lives in this
module so that migration touches one file.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

from .tracker import PositionFix

SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    icao        TEXT PRIMARY KEY,
    first_seen  REAL NOT NULL,          -- unix seconds
    last_seen   REAL NOT NULL,
    callsign    TEXT                    -- last callsign heard
);

CREATE TABLE IF NOT EXISTS positions (
    id                 INTEGER PRIMARY KEY,
    icao               TEXT NOT NULL REFERENCES aircraft(icao),
    ts                 REAL NOT NULL,   -- receive time, unix seconds
    lat                REAL NOT NULL,
    lon                REAL NOT NULL,
    altitude_ft        INTEGER,
    ground_speed_kt    REAL,
    track_deg          REAL,
    vertical_rate_fpm  INTEGER,
    on_ground          INTEGER
);

CREATE INDEX IF NOT EXISTS positions_icao_ts ON positions (icao, ts);
CREATE INDEX IF NOT EXISTS positions_ts ON positions (ts);
"""

AircraftRow = tuple[str, float, float, str | None]  # icao, first_seen, last_seen, callsign

_UPSERT_AIRCRAFT = """
INSERT INTO aircraft (icao, first_seen, last_seen, callsign) VALUES (?, ?, ?, ?)
ON CONFLICT (icao) DO UPDATE SET
    first_seen = min(first_seen, excluded.first_seen),
    last_seen  = max(last_seen, excluded.last_seen),
    callsign   = coalesce(excluded.callsign, callsign)
"""

_INSERT_POSITION = """
INSERT INTO positions (icao, ts, lat, lon, altitude_ft, ground_speed_kt, track_deg,
                       vertical_rate_fpm, on_ground)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Writes happen in a worker thread (asyncio.to_thread) so the event loop never blocks
        # on disk I/O; the lock guarantees only one thread uses the connection at a time.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)

    def write(self, aircraft: Sequence[AircraftRow], fixes: Sequence[PositionFix]) -> None:
        """Write one batch atomically: aircraft first, so position foreign keys resolve."""
        position_rows = [
            (f.icao, f.ts, f.lat, f.lon, f.altitude_ft, f.ground_speed_kt, f.track_deg,
             f.vertical_rate_fpm, None if f.on_ground is None else int(f.on_ground))
            for f in fixes
        ]
        with self._lock, self._conn:  # `with conn` = one transaction, rolled back on error
            self._conn.executemany(_UPSERT_AIRCRAFT, aircraft)
            self._conn.executemany(_INSERT_POSITION, position_rows)

    def query(self, sql: str, params: Sequence[object] = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
