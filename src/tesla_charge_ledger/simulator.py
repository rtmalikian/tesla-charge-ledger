"""Simulated charging month for tests, demos, and the GitHub Pages POC.
No API calls, no real cars. One pretend Model Y, September 2026:

- Sep 3: overnight home session, all off-peak (cheap).
- Sep 7: home session 3–7 PM, crossing into the 4–9 PM peak.
- Sep 12: Supercharger road-trip session (flat rate).
- Sep 18: overnight home session, all off-peak.
- Sep 22: public charger session (flat rate).
- Sep 27: home session 5–8 PM, entirely inside the peak.
- Sep 30: overnight home session, all off-peak.

Core invariant the e2e test asserts: the month's reported total cost
equals the sum of the individual session costs, and the peak-crossing
Sep 7 session costs more per kWh than the all-off-peak Sep 3 session.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .ledger import (ChargeSession, month_key, price_session, report_text,
                     summarize)

DEMO_MONTH = "2026-09"


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def demo_sessions_raw() -> list[ChargeSession]:
    car = "Model Y"
    return [
        ChargeSession(car, _at(3, 23), _at(4, 6), 30, 78, 45.0,
                      "home", "Home"),
        ChargeSession(car, _at(7, 15), _at(7, 19), 40, 72, 30.0,
                      "home", "Home"),
        ChargeSession(car, _at(12, 11, 30), _at(12, 12, 15), 15, 75, 55.0,
                      "supercharger", "Tejon Ranch Supercharger"),
        ChargeSession(car, _at(18, 1), _at(18, 5), 35, 80, 40.0,
                      "home", "Home"),
        ChargeSession(car, _at(22, 10), _at(22, 11), 45, 72, 25.0,
                      "public", "Target parking charger"),
        ChargeSession(car, _at(27, 17), _at(27, 20), 50, 72, 20.0,
                      "home", "Home"),
        # Sep 30, 10 PM → Oct 1, 2 AM: crosses midnight, still off-peak.
        ChargeSession(car, _at(30, 22),
                      datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc), 25, 65,
                      35.0, "home", "Home"),
    ]


def run_demo_month(settings) -> list[ChargeSession]:
    """Price the demo month with the caller's rates. Returns sessions."""
    sessions = demo_sessions_raw()
    for s in sessions:
        price_session(s, settings.tou_periods,
                      settings.supercharger_rate, settings.public_rate)
    return sessions


def timeline_lines(settings) -> list[str]:
    """Human-readable demo timeline for `tcledger simulate`."""
    sessions = run_demo_month(settings)
    lines = ["Tesla Charge Ledger — demo month (pretend car, pretend sessions)"]
    for s in sessions:
        lines.append(
            f"{s.start.strftime('%a %b %d, %I:%M %p')} — {s.vehicle} at "
            f"{s.location_name or s.location_class}: {s.kwh_added:.0f} kWh, "
            f"{s.start_soc:.0f}% → {s.end_soc:.0f}%, "
            f"${(s.cost_usd or 0):.2f}")
    lines.append("")
    lines.append(report_text(DEMO_MONTH, sessions))
    lines.append("")
    lines.append("Why the Sep 7 session costs more per kWh than Sep 3:")
    lines.append("Sep 3 charged overnight at the cheap off-peak rate; Sep 7")
    lines.append("ran 3–7 PM, so three of its four hours fell in the 4–9 PM")
    lines.append("peak window. Same car, same plug — the clock set the price.")
    lines.append("")
    lines.append("Expected Fleet API cost: this app only reads charge state")
    lines.append("while the car is already awake and charging — no wake-ups,")
    lines.append("no commands, so the real bill stays near zero. The ledger")
    lines.append("itself lives on your computer, not in Tesla's cloud.")
    return lines
