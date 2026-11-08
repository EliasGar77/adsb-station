"""Generate a SIMULATED recording of air traffic around Granada.

This exists only so the pipeline can be developed and tested before the receiver hardware
arrives. Every simulated aircraft uses a callsign starting with "SIM" and the file header
says "# source: simulated", so simulated data can never be mistaken for real traffic.

The model is deliberately simple: straight-line flights at constant ground speed on a flat
local grid (fine at a few hundred km), with reception probability falling with distance.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..recording import format_line

RECEIVER = (37.1773, -3.5986)  # Granada city centre
AIRPORT = (37.1887, -3.7774)  # LEGR, Federico Garcia Lorca Granada-Jaen
MAX_RANGE_KM = 250.0
KM_PER_DEG_LAT = 111.32
KT_TO_KM_S = 1.852 / 3600.0
DEFAULT_START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()


@dataclass(frozen=True)
class Flight:
    icao: str
    callsign: str
    squawk: str
    start_t: float  # seconds from the start of the recording (may be negative)
    start_km: tuple[float, float]  # (east, north) relative to the receiver
    end_km: tuple[float, float]
    alt_start_ft: int
    alt_end_ft: int
    ground_speed_kt: float

    @property
    def length_km(self) -> float:
        return math.dist(self.start_km, self.end_km)

    @property
    def duration_s(self) -> float:
        return self.length_km / (self.ground_speed_kt * KT_TO_KM_S)

    @property
    def track_deg(self) -> float:
        dx = self.end_km[0] - self.start_km[0]
        dy = self.end_km[1] - self.start_km[1]
        return math.degrees(math.atan2(dx, dy)) % 360.0

    @property
    def vertical_rate_fpm(self) -> int:
        return round((self.alt_end_ft - self.alt_start_ft) / (self.duration_s / 60.0))

    def state_at(self, t: float) -> tuple[float, float, int] | None:
        """(east_km, north_km, altitude_ft) at time t, or None if not airborne then."""
        progress = (t - self.start_t) / self.duration_s
        if not 0.0 <= progress <= 1.0:
            return None
        x = self.start_km[0] + (self.end_km[0] - self.start_km[0]) * progress
        y = self.start_km[1] + (self.end_km[1] - self.start_km[1]) * progress
        alt = self.alt_start_ft + (self.alt_end_ft - self.alt_start_ft) * progress
        return x, y, round(alt / 25) * 25  # ADS-B altitude has 25 ft resolution


def km_to_latlon(x_km: float, y_km: float) -> tuple[float, float]:
    lat0, lon0 = RECEIVER
    lat = lat0 + y_km / KM_PER_DEG_LAT
    lon = lon0 + x_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return lat, lon


def latlon_to_km(lat: float, lon: float) -> tuple[float, float]:
    lat0, lon0 = RECEIVER
    return (
        (lon - lon0) * KM_PER_DEG_LAT * math.cos(math.radians(lat0)),
        (lat - lat0) * KM_PER_DEG_LAT,
    )


def _point_on_circle(bearing_deg: float, radius_km: float) -> tuple[float, float]:
    b = math.radians(bearing_deg)
    return radius_km * math.sin(b), radius_km * math.cos(b)


def random_flight(rng: random.Random, index: int, start_t: float) -> Flight:
    icao = f"{rng.randrange(0x340000, 0x380000):06X}"  # Spanish allocation block
    callsign = f"SIM{index:03d}"
    squawk = "".join(str(rng.randrange(8)) for _ in range(4))  # octal digits
    airport = latlon_to_km(*AIRPORT)
    kind = rng.choices(["overflight", "arrival", "departure"], weights=[6, 2, 2])[0]
    if kind == "overflight":
        entry = rng.uniform(0, 360)
        exit_ = entry + 180 + rng.uniform(-60, 60)
        start = _point_on_circle(entry, MAX_RANGE_KM)
        end = _point_on_circle(exit_, MAX_RANGE_KM)
        alt = rng.randrange(280, 400, 10) * 100
        return Flight(icao, callsign, squawk, start_t, start, end, alt, alt,
                      rng.uniform(380, 480))
    far = _point_on_circle(rng.uniform(0, 360), 150.0)
    far = (far[0] + airport[0], far[1] + airport[1])
    high = rng.randrange(180, 260, 10) * 100
    speed = rng.uniform(200, 260)
    if kind == "arrival":
        return Flight(icao, callsign, squawk, start_t, far, airport, high, 2000, speed)
    return Flight(icao, callsign, squawk, start_t, airport, far, 2000, high, speed)


def sbs_line(tt: int, icao: str, when: datetime, *, callsign: str = "", altitude: str = "",
             speed: str = "", track: str = "", lat: str = "", lon: str = "", vrate: str = "",
             squawk: str = "", on_ground: str = "0") -> str:
    date, time = when.strftime("%Y/%m/%d"), when.strftime("%H:%M:%S.%f")[:-3]
    return ",".join([
        "MSG", str(tt), "1", "1", icao, "1", date, time, date, time,
        callsign, altitude, speed, track, lat, lon, vrate, squawk, "0", "0", "0", on_ground,
    ])


def simulate(minutes: float, aircraft: int, seed: int, start: float = DEFAULT_START,
             tick_s: float = 0.5) -> list[tuple[float, str]]:
    """Return (unix_ts, sbs_line) pairs sorted by time."""
    rng = random.Random(seed)
    duration = minutes * 60.0
    flights = [random_flight(rng, i + 1, rng.uniform(-1800, duration)) for i in range(aircraft)]
    events: list[tuple[float, str]] = []
    steps = int(duration / tick_s)
    for flight in flights:
        last_ident = -math.inf
        for step in range(steps):
            t = step * tick_s
            state = flight.state_at(t)
            if state is None:
                continue
            x, y, alt = state
            distance = math.hypot(x, y)
            if distance > MAX_RANGE_KM:
                continue
            # Reception gets worse with distance; never perfect even close by.
            p_receive = 0.85 * max(0.15, 1.0 - distance / MAX_RANGE_KM)
            ts = start + t + rng.uniform(0, tick_s * 0.9)
            when = datetime.fromtimestamp(ts, UTC)
            if rng.random() < p_receive:
                lat, lon = km_to_latlon(x, y)
                events.append((ts, sbs_line(3, flight.icao, when, altitude=str(alt),
                                            lat=f"{lat:.5f}", lon=f"{lon:.5f}")))
            if step % 2 == 0 and rng.random() < p_receive:
                events.append((ts + 0.01, sbs_line(
                    4, flight.icao, when, speed=f"{flight.ground_speed_kt:.0f}",
                    track=f"{flight.track_deg:.1f}", vrate=str(flight.vertical_rate_fpm))))
            if t - last_ident >= 5.0 and rng.random() < p_receive:
                events.append((ts + 0.02, sbs_line(1, flight.icao, when,
                                                   callsign=flight.callsign)))
                last_ident = t
            if step % 10 == 0 and rng.random() < p_receive:
                events.append((ts + 0.03, sbs_line(6, flight.icao, when, altitude=str(alt),
                                                   squawk=flight.squawk)))
    events.sort(key=lambda event: event[0])
    return events


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--aircraft", type=int, default=25,
                        help="flights generated over the window (not all visible at once)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("data/recordings/simulated.rec"))
    args = parser.parse_args(argv)

    events = simulate(args.minutes, args.aircraft, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="ascii", newline="\n") as handle:
        handle.write(f"# source: simulated (seed={args.seed}, minutes={args.minutes}, "
                     f"aircraft={args.aircraft})\n")
        for ts, line in events:
            handle.write(format_line(ts, line))
    print(f"wrote {len(events)} lines to {args.out}")


if __name__ == "__main__":
    main()
