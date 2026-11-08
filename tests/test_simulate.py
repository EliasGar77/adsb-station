import math

from adsb_station.sbs import parse_line
from adsb_station.tools.simulate import MAX_RANGE_KM, latlon_to_km, simulate


def test_simulation_is_deterministic():
    assert simulate(minutes=1, aircraft=5, seed=7) == simulate(minutes=1, aircraft=5, seed=7)
    assert simulate(minutes=1, aircraft=5, seed=7) != simulate(minutes=1, aircraft=5, seed=8)


def test_simulated_lines_are_valid_and_clearly_labelled():
    events = simulate(minutes=3, aircraft=15, seed=1)
    assert events, "expected some traffic"
    timestamps = [ts for ts, _ in events]
    assert timestamps == sorted(timestamps)

    for _, line in events:
        msg = parse_line(line)
        assert msg is not None
        if msg.callsign is not None:
            assert msg.callsign.startswith("SIM")
        if msg.has_position:
            x, y = latlon_to_km(msg.lat, msg.lon)
            assert math.hypot(x, y) <= MAX_RANGE_KM + 1
