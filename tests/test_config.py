import pytest

from tesla_charge_ledger.config import (ConfigError, format_12h_time,
                                       load_settings, parse_12h_time,
                                       parse_tou_rates)

BASE = {"TCL_VEHICLES": "Model Y=VINY,Model 3=VIN3"}


def test_defaults():
    s = load_settings(BASE)
    assert s.server_port == 8648
    assert s.model_id == "tesla-charge-ledger"
    assert s.supercharger_rate == pytest.approx(0.45)
    assert s.public_rate == pytest.approx(0.40)
    assert len(s.vehicles) == 2
    assert s.vehicles[0].name == "Model Y"
    # Default TOU table: off-peak overnight, peak 4–9 PM.
    assert len(s.tou_periods) == 3
    peak = [p for p in s.tou_periods if p.name == "peak"][0]
    assert peak.rate_per_kwh == pytest.approx(0.55)


def test_12h_parsing_and_roundtrip():
    assert parse_12h_time("4:00 PM") == 16 * 60
    assert parse_12h_time("12:00 AM") == 0
    assert parse_12h_time("12:00 PM") == 12 * 60
    assert parse_12h_time("5:05 am") == 5 * 60 + 5
    assert format_12h_time(16 * 60) == "4:00 PM"
    assert format_12h_time(0) == "12:00 AM"


def test_bad_times_rejected_with_human_message():
    with pytest.raises(ConfigError, match="4:00 PM"):
        parse_tou_rates("peak|16:00|9:00 PM|0.55")
    with pytest.raises(ConfigError, match="13:00 PM"):
        parse_tou_rates("peak|13:00 PM|9:00 PM|0.55")
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCL_AI_MODE": "sometimes"})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCL_TOU_RATES": "peak|4:00 PM|9:00 PM"})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCL_SUPERCHARGER_RATE": "0"})


def test_midnight_crossing_period():
    periods = parse_tou_rates("night|9:00 PM|12:00 AM|0.20")
    assert periods[0].covers(22 * 60 + 30)
    assert periods[0].covers(23 * 60 + 59)
    assert not periods[0].covers(0)
    assert not periods[0].covers(12 * 60)


def test_bad_region_and_vehicles():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TESLA_REGION": "xx"})
    with pytest.raises(ConfigError):
        load_settings({"TCL_VEHICLES": "Model Y="})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCL_EMBEDDING_DIM": "4"})


def test_flat_and_custom_rates_load():
    flat = "flat|12:00 AM|12:00 PM|0.30;flat|12:00 PM|12:00 AM|0.30"
    s = load_settings({**BASE, "TCL_TOU_RATES": flat})
    assert len(s.tou_periods) == 2
    assert all(p.rate_per_kwh == pytest.approx(0.30) for p in s.tou_periods)
