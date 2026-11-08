from adsb_station.sbs import SbsMessage
from adsb_station.tracker import Tracker


def test_state_is_merged_across_message_types():
    tracker = Tracker()
    tracker.update(SbsMessage(1, "4CA2D6", callsign="RYR4KD"), now=100.0)
    tracker.update(SbsMessage(4, "4CA2D6", ground_speed_kt=450, track_deg=210.0,
                              vertical_rate_fpm=0), now=100.5)
    aircraft, fix = tracker.update(
        SbsMessage(3, "4CA2D6", altitude_ft=37000, lat=37.1, lon=-3.6), now=101.0)

    assert aircraft.callsign == "RYR4KD"
    assert aircraft.message_count == 3
    assert (aircraft.first_seen, aircraft.last_seen) == (100.0, 101.0)
    # The fix carries the latest velocity even though it came in a different message.
    assert fix.ts == 101.0
    assert (fix.lat, fix.lon, fix.altitude_ft) == (37.1, -3.6, 37000)
    assert (fix.ground_speed_kt, fix.track_deg) == (450, 210.0)


def test_only_messages_with_position_produce_fixes():
    tracker = Tracker()
    _, fix = tracker.update(SbsMessage(5, "34510C", altitude_ft=8000), now=1.0)
    assert fix is None
    assert tracker.get("34510C").altitude_ft == 8000


def test_missing_fields_do_not_erase_known_values():
    tracker = Tracker()
    tracker.update(SbsMessage(1, "34510C", callsign="IBE123"), now=1.0)
    tracker.update(SbsMessage(3, "34510C", lat=37.0, lon=-3.0), now=2.0)
    assert tracker.get("34510C").callsign == "IBE123"


def test_aircraft_are_tracked_independently():
    tracker = Tracker()
    tracker.update(SbsMessage(1, "AAAAAA", callsign="ONE"), now=1.0)
    tracker.update(SbsMessage(1, "BBBBBB", callsign="TWO"), now=1.0)
    assert len(tracker) == 2
    assert tracker.get("AAAAAA").callsign == "ONE"


def test_expire_removes_only_stale_aircraft():
    tracker = Tracker(timeout_s=60)
    tracker.update(SbsMessage(1, "AAAAAA", callsign="OLD"), now=0.0)
    tracker.update(SbsMessage(1, "BBBBBB", callsign="NEW"), now=50.0)

    expired = tracker.expire(now=61.0)

    assert [a.icao for a in expired] == ["AAAAAA"]
    assert tracker.get("AAAAAA") is None
    assert tracker.get("BBBBBB") is not None
