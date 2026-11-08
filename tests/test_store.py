import sqlite3

import pytest

from adsb_station.store import Store
from adsb_station.tracker import PositionFix


def fix(icao, ts, lat=37.0, lon=-3.6):
    return PositionFix(icao, ts, lat, lon, 30000, 420.0, 90.0, 0, False)


def test_write_and_read_back(tmp_path):
    store = Store(tmp_path / "adsb.sqlite3")
    store.write([("4CA2D6", 10.0, 12.0, "RYR4KD")], [fix("4CA2D6", 11.0), fix("4CA2D6", 12.0)])

    assert store.query("SELECT icao, first_seen, last_seen, callsign FROM aircraft") == [
        ("4CA2D6", 10.0, 12.0, "RYR4KD")
    ]
    rows = store.query("SELECT icao, ts, altitude_ft, on_ground FROM positions ORDER BY ts")
    assert rows == [("4CA2D6", 11.0, 30000, 0), ("4CA2D6", 12.0, 30000, 0)]


def test_upsert_keeps_first_seen_and_known_callsign():
    store = Store(":memory:")
    store.write([("4CA2D6", 10.0, 12.0, "RYR4KD")], [])
    store.write([("4CA2D6", 50.0, 60.0, None)], [])  # later batch, callsign not heard

    assert store.query("SELECT first_seen, last_seen, callsign FROM aircraft") == [
        (10.0, 60.0, "RYR4KD")
    ]


def test_batch_is_atomic(tmp_path):
    store = Store(tmp_path / "adsb.sqlite3")
    # Position for an aircraft that is not in the batch violates the foreign key:
    # nothing from the batch may be written.
    with pytest.raises(sqlite3.IntegrityError):
        store.write([("AAAAAA", 1.0, 1.0, None)], [fix("BBBBBB", 1.0)])
    assert store.query("SELECT count(*) FROM aircraft") == [(0,)]
    assert store.query("SELECT count(*) FROM positions") == [(0,)]
