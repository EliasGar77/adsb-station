"""Merge individual SBS messages into the current state of each aircraft.

A single SBS message only carries part of the picture: MSG,1 has the callsign, MSG,3 the
position and altitude, MSG,4 the velocity... The tracker keeps the latest known value of
every field per aircraft, and emits a PositionFix each time a new position arrives.
"""

from __future__ import annotations

from dataclasses import dataclass

from .sbs import SbsMessage

# Fields copied from a message into the aircraft state when present.
_STATE_FIELDS = (
    "callsign",
    "altitude_ft",
    "ground_speed_kt",
    "track_deg",
    "lat",
    "lon",
    "vertical_rate_fpm",
    "squawk",
    "on_ground",
)


@dataclass(slots=True)
class Aircraft:
    icao: str
    first_seen: float
    last_seen: float
    message_count: int = 0
    callsign: str | None = None
    altitude_ft: int | None = None
    ground_speed_kt: float | None = None
    track_deg: float | None = None
    lat: float | None = None
    lon: float | None = None
    vertical_rate_fpm: int | None = None
    squawk: str | None = None
    on_ground: bool | None = None
    last_position_at: float | None = None


@dataclass(frozen=True, slots=True)
class PositionFix:
    """One point of an aircraft's track, with the latest known kinematics at that time."""

    icao: str
    ts: float
    lat: float
    lon: float
    altitude_ft: int | None
    ground_speed_kt: float | None
    track_deg: float | None
    vertical_rate_fpm: int | None
    on_ground: bool | None


class Tracker:
    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self._aircraft: dict[str, Aircraft] = {}

    def __len__(self) -> int:
        return len(self._aircraft)

    def get(self, icao: str) -> Aircraft | None:
        return self._aircraft.get(icao)

    def aircraft(self) -> list[Aircraft]:
        return list(self._aircraft.values())

    def update(self, message: SbsMessage, now: float) -> tuple[Aircraft, PositionFix | None]:
        """Apply a message received at `now` (unix seconds).

        Returns the updated aircraft and, if the message carried a position, the new fix.
        """
        aircraft = self._aircraft.get(message.icao)
        if aircraft is None:
            aircraft = Aircraft(icao=message.icao, first_seen=now, last_seen=now)
            self._aircraft[message.icao] = aircraft

        aircraft.last_seen = max(aircraft.last_seen, now)
        aircraft.message_count += 1
        for name in _STATE_FIELDS:
            value = getattr(message, name)
            if value is not None:
                setattr(aircraft, name, value)

        if not message.has_position:
            return aircraft, None
        aircraft.last_position_at = now
        fix = PositionFix(
            icao=aircraft.icao,
            ts=now,
            lat=message.lat,
            lon=message.lon,
            altitude_ft=aircraft.altitude_ft,
            ground_speed_kt=aircraft.ground_speed_kt,
            track_deg=aircraft.track_deg,
            vertical_rate_fpm=aircraft.vertical_rate_fpm,
            on_ground=aircraft.on_ground,
        )
        return aircraft, fix

    def expire(self, now: float) -> list[Aircraft]:
        """Forget aircraft not heard from in `timeout_s` seconds and return them."""
        stale = [a for a in self._aircraft.values() if now - a.last_seen > self.timeout_s]
        for aircraft in stale:
            del self._aircraft[aircraft.icao]
        return stale
