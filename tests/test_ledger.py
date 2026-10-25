"""Pricing and ledger logic: TOU splits, flat public rates, validation,
reports, and the CSV export."""
from datetime import datetime, timezone

import pytest

from tesla_charge_ledger.config import parse_tou_rates
from tesla_charge_ledger.ledger import (ChargeSession, LedgerError, append_session,
                                       export_csv, filter_month, load_sessions,
                                       month_key, parse_month_query,
                                       price_session, report_text, summarize)

TOU = parse_tou_rates(
    "off-peak|12:00 AM|4:00 PM|0.28;"
    "peak|4:00 PM|9:00 PM|0.55;"
    "off-peak|9:00 PM|12:00 AM|0.28")


def _at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _session(start, end, kwh, loc="home", car="Model Y"):
    return ChargeSession(car, start, end, 30, 80, kwh, loc, "Home")


def test_off_peak_session_priced_at_off_peak_rate():
    s = price_session(_session(_at(3, 23), _at(4, 6), 45.0), TOU, 0.45, 0.40)
    assert s.cost_usd == pytest.approx(45.0 * 0.28, abs=0.05)
    assert "peak" not in s.cost_breakdown


def test_peak_crossing_session_costs_more_per_kwh():
    cheap = price_session(_session(_at(3, 23), _at(4, 6), 40.0), TOU, 0.45, 0.40)
    pricey = price_session(_session(_at(7, 15), _at(7, 19), 40.0), TOU, 0.45, 0.40)
    # Same energy, same length — the 3–7 PM session buys 3 peak hours.
    assert pricey.cost_usd > cheap.cost_usd
    assert pricey.cost_usd == pytest.approx(10.0 * 0.28 + 30.0 * 0.55, abs=0.05)
    assert pricey.cost_breakdown["peak"][0] == pytest.approx(30.0, abs=0.1)


def test_all_peak_session_and_midnight_crossing():
    s = price_session(_session(_at(27, 17), _at(27, 20), 20.0), TOU, 0.45, 0.40)
    assert s.cost_usd == pytest.approx(20.0 * 0.55, abs=0.05)
    overnight = ChargeSession("Model Y", _at(30, 22),
                              datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc),
                              25, 65, 35.0, "home", "Home")
    overnight = price_session(overnight, TOU, 0.45, 0.40)
    assert overnight.cost_usd == pytest.approx(35.0 * 0.28, abs=0.05)


def test_flat_supercharger_and_public_rates():
    sc = price_session(_session(_at(12, 11, 30), _at(12, 12, 15), 55.0,
                                "supercharger"), TOU, 0.45, 0.40)
    assert sc.cost_usd == pytest.approx(55.0 * 0.45)
    pub = price_session(_session(_at(22, 10), _at(22, 11), 25.0, "public"),
                        TOU, 0.45, 0.40)
    assert pub.cost_usd == pytest.approx(25.0 * 0.40)


def test_zero_kwh_session_costs_zero():
    s = price_session(_session(_at(3, 23), _at(4, 6), 0.0), TOU, 0.45, 0.40)
    assert s.cost_usd == 0.0


def test_invalid_sessions_rejected_with_human_messages():
    with pytest.raises(LedgerError, match="after the start"):
        price_session(_session(_at(4, 6), _at(3, 23), 10.0), TOU, 0.45, 0.40)
    with pytest.raises(LedgerError, match="negative"):
        price_session(_session(_at(3, 23), _at(4, 6), -5.0), TOU, 0.45, 0.40)
    with pytest.raises(LedgerError, match="between 0 and 100"):
        s = _session(_at(3, 23), _at(4, 6), 10.0)
        s.start_soc = 120
        price_session(s, TOU, 0.45, 0.40)
    with pytest.raises(LedgerError, match="home, supercharger, or public"):
        price_session(_session(_at(3, 23), _at(4, 6), 10.0, "work"),
                      TOU, 0.45, 0.40)


def test_month_filter_summary_and_report(tmp_path):
    sessions = [
        price_session(_session(_at(3, 23), _at(4, 6), 45.0), TOU, 0.45, 0.40),
        price_session(_session(_at(12, 11, 30), _at(12, 12, 15), 55.0,
                               "supercharger"), TOU, 0.45, 0.40),
    ]
    assert month_key(sessions[0].start) == "2026-09"
    sept = filter_month(sessions, "2026-09")
    assert len(sept) == 2
    s = summarize(sept)
    assert s["kwh"] == pytest.approx(100.0)
    assert s["cost"] == pytest.approx(sum(x.cost_usd for x in sept))
    text = report_text("2026-09", sessions)
    assert "September 2026" in text
    assert "supercharger" in text
    assert report_text("2026-10", sessions).startswith("No charging sessions")


def test_month_query_parsing():
    sessions = [_session(_at(3, 23), _at(4, 6), 45.0)]
    assert parse_month_query("what did charging cost in September?", sessions) == "2026-09"
    assert parse_month_query("September 2026", sessions) == "2026-09"
    assert parse_month_query("2026-09", sessions) == "2026-09"
    assert parse_month_query("this month", sessions) == "2026-09"
    assert parse_month_query("last month", sessions) == "2026-08"
    assert parse_month_query("how is the weather?", sessions) is None


def test_jsonl_roundtrip_and_csv_export(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    s = price_session(_session(_at(3, 23), _at(4, 6), 45.0), TOU, 0.45, 0.40)
    append_session(path, s)
    loaded = load_sessions(path)
    assert len(loaded) == 1
    assert loaded[0].cost_usd == s.cost_usd
    csv_path = str(tmp_path / "out.csv")
    assert export_csv(loaded, csv_path) == 1
    rows = (tmp_path / "out.csv").read_text().splitlines()
    assert rows[0].startswith("date,vehicle")
    assert "2026-09-03" in rows[1]
    assert load_sessions(str(tmp_path / "missing.jsonl")) == []
