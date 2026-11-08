from datetime import datetime

import pytest

from adsb_station.sbs import SbsParseError, parse_line

# Lines in the exact shape dump1090/readsb produce.
IDENT = "MSG,1,1,1,4CA2D6,1,2026/11/08,12:00:00.100,2026/11/08,12:00:00.100,RYR4KD  ,,,,,,,,0,0,0,0"
POSITION = (
    "MSG,3,1,1,4CA2D6,1,2026/11/08,12:00:00.500,2026/11/08,12:00:00.500,"
    ",37000,,,37.12345,-3.54321,,,0,0,0,0"
)
VELOCITY = (
    "MSG,4,1,1,4CA2D6,1,2026/11/08,12:00:01.000,2026/11/08,12:00:01.000,"
    ",,452,213.4,,,-64,,0,0,0,0"
)
SQUAWK = (
    "MSG,6,1,1,34510C,1,2026/11/08,12:00:02.000,2026/11/08,12:00:02.000,"
    ",8000,,,,,,7700,-1,-1,0,0"
)


def test_identification_message_has_trimmed_callsign():
    msg = parse_line(IDENT)
    assert msg.transmission_type == 1
    assert msg.icao == "4CA2D6"
    assert msg.callsign == "RYR4KD"
    assert not msg.has_position


def test_airborne_position():
    msg = parse_line(POSITION)
    assert msg.transmission_type == 3
    assert msg.altitude_ft == 37000
    assert (msg.lat, msg.lon) == (37.12345, -3.54321)
    assert msg.has_position
    assert msg.on_ground is False
    assert msg.generated_at == datetime(2026, 11, 8, 12, 0, 0, 500000)


def test_velocity():
    msg = parse_line(VELOCITY)
    assert msg.ground_speed_kt == 452
    assert msg.track_deg == 213.4
    assert msg.vertical_rate_fpm == -64
    assert msg.lat is None


def test_squawk_and_minus_one_flags_mean_true():
    msg = parse_line(SQUAWK)
    assert msg.squawk == "7700"
    assert msg.alert is True
    assert msg.emergency is True
    assert msg.spi is False


def test_lowercase_icao_is_normalised():
    assert parse_line(POSITION.replace("4CA2D6", "4ca2d6")).icao == "4CA2D6"


def test_non_icao_address_from_readsb_is_kept():
    assert parse_line(POSITION.replace("4CA2D6", "~3C1234")).icao == "~3C1234"


@pytest.mark.parametrize("line", ["", "   ", "STA,,5,179,400AE7,10103,2008/11/28,14:58:51",
                                  "CLK,,,,,,,,,,,,,,,,,,,,,"])
def test_lines_without_aircraft_data_are_ignored(line):
    assert parse_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "MSG,3,1,1,4CA2D6",                                    # truncated
        POSITION.replace("MSG,3", "MSG,9"),                     # unknown type
        POSITION.replace("MSG,3", "MSG,x"),
        POSITION.replace("4CA2D6", "4CA2"),                     # short address
        POSITION.replace("4CA2D6", "ZZZZZZ"),                   # not hex
        POSITION.replace("37.12345", "137.12345"),              # latitude out of range
        POSITION.replace("37.12345", "nan"),
        POSITION.replace("37000", "high"),
        POSITION.replace(",-3.54321,", ",,"),                   # lat without lon
    ],
)
def test_malformed_lines_raise(line):
    with pytest.raises(SbsParseError):
        parse_line(line)


def test_missing_timestamp_is_tolerated():
    fields = POSITION.split(",")
    fields[6] = fields[7] = ""  # empty generated date and time
    msg = parse_line(",".join(fields))
    assert msg.generated_at is None
    assert msg.has_position
