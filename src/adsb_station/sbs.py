"""Parser for the SBS-1 "BaseStation" text format.

dump1090 and readsb serve already-decoded messages in this format on TCP port 30003.
Each line is 22 comma-separated fields:

    MSG,3,1,1,4CA2D6,1,2026/11/08,12:00:00.500,2026/11/08,12:00:00.500,
    ,37000,,,37.12345,-3.54321,,,0,0,0,0          (one line on the wire, split here)

    1 message type    (MSG, SEL, ID, AIR, STA, CLK; only MSG carries aircraft data)
    2 transmission    (1-8, see TRANSMISSION_TYPES)
    5 ICAO 24-bit address in hex (readsb prefixes "~" for non-ICAO/TIS-B addresses)
    7-8 date/time the message was generated, 9-10 date/time it was logged
    11 callsign, 12 altitude (ft), 13 ground speed (kt), 14 track (deg),
    15 latitude, 16 longitude, 17 vertical rate (ft/min), 18 squawk,
    19 squawk-change alert, 20 emergency, 21 SPI (ident), 22 on ground
    Flags are "-1" (true) or "0" (false); empty fields mean "not in this message".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

TRANSMISSION_TYPES = {
    1: "ES identification and category",
    2: "ES surface position",
    3: "ES airborne position",
    4: "ES airborne velocity",
    5: "surveillance altitude",
    6: "surveillance ID (squawk)",
    7: "air-to-air",
    8: "all-call reply",
}

_FIELD_COUNT = 22
_HEX_DIGITS = frozenset("0123456789ABCDEF")


class SbsParseError(ValueError):
    """A line looked like an SBS MSG line but could not be parsed."""


@dataclass(frozen=True, slots=True)
class SbsMessage:
    transmission_type: int
    icao: str
    generated_at: datetime | None = None
    callsign: str | None = None
    altitude_ft: int | None = None
    ground_speed_kt: float | None = None
    track_deg: float | None = None
    lat: float | None = None
    lon: float | None = None
    vertical_rate_fpm: int | None = None
    squawk: str | None = None
    alert: bool | None = None
    emergency: bool | None = None
    spi: bool | None = None
    on_ground: bool | None = None

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None


def parse_line(line: str) -> SbsMessage | None:
    """Parse one SBS line.

    Returns None for blank lines and non-MSG lines (they carry no aircraft state).
    Raises SbsParseError for MSG lines that are malformed.
    """
    line = line.strip()
    if not line:
        return None
    fields = line.split(",")
    if fields[0] != "MSG":
        return None
    if len(fields) < _FIELD_COUNT:
        raise SbsParseError(f"expected {_FIELD_COUNT} fields, got {len(fields)}: {line!r}")

    try:
        transmission_type = int(fields[1])
    except ValueError as exc:
        raise SbsParseError(f"bad transmission type {fields[1]!r}") from exc
    if transmission_type not in TRANSMISSION_TYPES:
        raise SbsParseError(f"unknown transmission type {transmission_type}")

    icao = fields[4].strip().upper()
    if not _is_valid_icao(icao):
        raise SbsParseError(f"bad ICAO address {fields[4]!r}")

    try:
        lat = _opt_float(fields[14])
        lon = _opt_float(fields[15])
        message = SbsMessage(
            transmission_type=transmission_type,
            icao=icao,
            generated_at=_opt_datetime(fields[6], fields[7]),
            callsign=_opt_str(fields[10]),
            altitude_ft=_opt_int(fields[11]),
            ground_speed_kt=_opt_float(fields[12]),
            track_deg=_opt_float(fields[13]),
            lat=lat,
            lon=lon,
            vertical_rate_fpm=_opt_int(fields[16]),
            squawk=_opt_str(fields[17]),
            alert=_opt_flag(fields[18]),
            emergency=_opt_flag(fields[19]),
            spi=_opt_flag(fields[20]),
            on_ground=_opt_flag(fields[21]),
        )
    except ValueError as exc:
        raise SbsParseError(f"{exc}: {line!r}") from exc

    if (lat is None) != (lon is None):
        raise SbsParseError(f"latitude and longitude must come together: {line!r}")
    if lat is not None and not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise SbsParseError(f"position out of range ({lat}, {lon})")
    return message


def _is_valid_icao(icao: str) -> bool:
    address = icao[1:] if icao.startswith("~") else icao
    return len(address) == 6 and set(address) <= _HEX_DIGITS


def _opt_str(raw: str) -> str | None:
    value = raw.strip()
    return value or None


def _opt_float(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite number {raw!r}")
    return number


def _opt_int(raw: str) -> int | None:
    number = _opt_float(raw)
    return None if number is None else round(number)


def _opt_flag(raw: str) -> bool | None:
    value = raw.strip()
    if not value:
        return None
    return value != "0"


def _opt_datetime(date: str, time: str) -> datetime | None:
    """Decoder timestamp. The format carries no timezone, so it is kept naive and only
    informative; the ingest service stamps every message with its own receive time."""
    date, time = date.strip(), time.strip()
    if not date or not time:
        return None
    for pattern in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date} {time}", pattern)
        except ValueError:
            continue
    return None
