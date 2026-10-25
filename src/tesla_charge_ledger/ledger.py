"""Charging-cost ledger: pure decision logic, no network, no clock
except the one callers pass in. Every function here is unit-testable.

Pricing model
-------------
* Home charging is priced against the owner's time-of-use (TOU) table:
  the session's energy is spread evenly across its duration, minute by
  minute, and each minute is priced at the TOU rate in force then. A
  session that runs into the 4–9 PM peak therefore costs more than the
  same energy drawn overnight — which is exactly the bill the utility
  sends.
* Supercharger and other public sessions are priced at a flat $/kWh
  (the rate the owner sets; Supercharger bills vary by site, so the
  owner can override per session later — the flat rate is the default,
  not a claim about Tesla's price list).
* A session's stored cost is computed once, at record time, from the
  rates then in force, so later rate edits never rewrite history.

Location classes: "home" | "supercharger" | "public".
"""
from __future__ import annotations

import calendar
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import TouPeriod

LOCATION_CLASSES = ("home", "supercharger", "public")

_MONTH_NAMES = [m.lower() for m in calendar.month_name[1:]]


class LedgerError(Exception):
    """Human-readable ledger problem (bad session, bad month, bad file)."""


@dataclass
class ChargeSession:
    vehicle: str
    start: datetime
    end: datetime
    start_soc: float       # percent, 0-100
    end_soc: float         # percent, 0-100
    kwh_added: float
    location_class: str    # home | supercharger | public
    location_name: str = ""
    cost_usd: float | None = None
    cost_breakdown: dict = field(default_factory=dict)  # period -> [kwh, $]

    def validate(self) -> None:
        if not self.vehicle.strip():
            raise LedgerError("A charge session needs a car name.")
        if self.location_class not in LOCATION_CLASSES:
            raise LedgerError(
                f"Charging location must be home, supercharger, or public — "
                f"got {self.location_class!r}. Pick one of the three.")
        if self.end <= self.start:
            raise LedgerError(
                "The charging end time must be after the start time — "
                "check the session's times.")
        if self.kwh_added < 0:
            raise LedgerError("Energy added can't be negative.")
        for label, soc in (("start", self.start_soc), ("end", self.end_soc)):
            if not (0 <= soc <= 100):
                raise LedgerError(
                    f"The {label} battery percentage must be between 0 and 100, "
                    f"got {soc}.")
        if self.end_soc < self.start_soc and self.kwh_added > 0:
            raise LedgerError(
                "The battery went down while charging added energy — "
                "check the start/end percentages.")

    # -- JSONL store ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "vehicle": self.vehicle,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "start_soc": self.start_soc,
            "end_soc": self.end_soc,
            "kwh_added": self.kwh_added,
            "location_class": self.location_class,
            "location_name": self.location_name,
            "cost_usd": self.cost_usd,
            "cost_breakdown": self.cost_breakdown,
        }

    @classmethod
    def from_dict(cls, row: dict) -> "ChargeSession":
        return cls(
            vehicle=row["vehicle"],
            start=datetime.fromisoformat(row["start"]),
            end=datetime.fromisoformat(row["end"]),
            start_soc=float(row["start_soc"]),
            end_soc=float(row["end_soc"]),
            kwh_added=float(row["kwh_added"]),
            location_class=row["location_class"],
            location_name=row.get("location_name", ""),
            cost_usd=row.get("cost_usd"),
            cost_breakdown=row.get("cost_breakdown") or {},
        )


def _rate_at(periods: list[TouPeriod], minute: int) -> float:
    """TOU rate in force at a minute of day; uncovered minutes fall back
    to the table's mean rate so a partial table never prices at $0."""
    for p in periods:
        if p.covers(minute):
            return p.rate_per_kwh
    return sum(p.rate_per_kwh for p in periods) / len(periods)


def _period_at(periods: list[TouPeriod], minute: int) -> str:
    for p in periods:
        if p.covers(minute):
            return p.name
    return "blended"


def price_session(session: ChargeSession, periods: list[TouPeriod],
                  supercharger_rate: float, public_rate: float) -> ChargeSession:
    """Compute and store the session's cost. Returns the same session."""
    session.validate()
    if session.location_class == "supercharger":
        session.cost_usd = round(session.kwh_added * supercharger_rate, 2)
        session.cost_breakdown = {
            "supercharger": [round(session.kwh_added, 2), session.cost_usd]}
        return session
    if session.location_class == "public":
        session.cost_usd = round(session.kwh_added * public_rate, 2)
        session.cost_breakdown = {
            "public": [round(session.kwh_added, 2), session.cost_usd]}
        return session
    # Home: spread energy evenly across the session, minute by minute.
    minutes = max(1, int((session.end - session.start).total_seconds() // 60))
    per_minute_kwh = session.kwh_added / minutes
    breakdown: dict[str, list[float]] = {}
    total = 0.0
    start_min = session.start.hour * 60 + session.start.minute
    for i in range(minutes):
        minute = (start_min + i) % (24 * 60)
        rate = _rate_at(periods, minute)
        name = _period_at(periods, minute)
        cost = per_minute_kwh * rate
        total += cost
        slot = breakdown.setdefault(name, [0.0, 0.0])
        slot[0] += per_minute_kwh
        slot[1] += cost
    session.cost_usd = round(total, 2)
    session.cost_breakdown = {k: [round(v[0], 2), round(v[1], 2)]
                              for k, v in breakdown.items()}
    return session


# -- store ------------------------------------------------------------
def append_session(path: str, session: ChargeSession) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(session.to_dict()) + "\n")


def load_sessions(path: str) -> list[ChargeSession]:
    p = Path(path).expanduser()
    if not p.exists():
        return []
    sessions = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sessions.append(ChargeSession.from_dict(json.loads(line)))
    return sessions


# -- reports ----------------------------------------------------------
def month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def filter_month(sessions: list[ChargeSession], key: str) -> list[ChargeSession]:
    return [s for s in sessions if month_key(s.start) == key]


def summarize(sessions: list[ChargeSession]) -> dict:
    total_kwh = sum(s.kwh_added for s in sessions)
    total_cost = sum(s.cost_usd or 0.0 for s in sessions)
    by_car: dict[str, dict] = {}
    by_class: dict[str, dict] = {}
    for s in sessions:
        car = by_car.setdefault(s.vehicle, {"sessions": 0, "kwh": 0.0, "cost": 0.0})
        car["sessions"] += 1
        car["kwh"] += s.kwh_added
        car["cost"] += s.cost_usd or 0.0
        loc = by_class.setdefault(s.location_class,
                                  {"sessions": 0, "kwh": 0.0, "cost": 0.0})
        loc["sessions"] += 1
        loc["kwh"] += s.kwh_added
        loc["cost"] += s.cost_usd or 0.0
    return {"sessions": len(sessions), "kwh": round(total_kwh, 2),
            "cost": round(total_cost, 2), "by_car": by_car, "by_class": by_class}


def month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{calendar.month_name[int(month)]} {year}"


def parse_month_query(text: str, sessions: list[ChargeSession]) -> str | None:
    """'September', 'September 2026', '2026-09', 'this month', 'last month'
    -> 'YYYY-MM'. Reference date = today, or the newest session when the
    ledger is a demo (so 'this month' works on pretend data too)."""
    q = text.lower()
    ref = max((s.start for s in sessions), default=datetime.now(timezone.utc))
    if "last month" in q:
        year, month = ref.year, ref.month - 1
        if month == 0:
            year, month = year - 1, 12
        return f"{year:04d}-{month:02d}"
    if "this month" in q:
        return month_key(ref)
    m = re.search(r"(\d{4})-(\d{1,2})", q)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    for i, name in enumerate(_MONTH_NAMES, start=1):
        if name in q:
            year_m = re.search(r"\b(20\d{2})\b", q)
            year = int(year_m.group(1)) if year_m else ref.year
            return f"{year:04d}-{i:02d}"
    return None


def report_text(key: str, sessions: list[ChargeSession]) -> str:
    month_sessions = filter_month(sessions, key)
    if not month_sessions:
        return (f"No charging sessions recorded in {month_label(key)} yet. "
                "Record one with `tcledger record`, or run `tcledger simulate` "
                "to see a demo month.")
    s = summarize(month_sessions)
    lines = [f"Charging in {month_label(key)}: {s['sessions']} sessions, "
             f"{s['kwh']:.1f} kWh, ${s['cost']:.2f}."]
    for car, row in sorted(s["by_car"].items()):
        lines.append(f"  {car}: {row['sessions']} sessions, {row['kwh']:.1f} kWh, "
                     f"${row['cost']:.2f}")
    for loc, row in sorted(s["by_class"].items()):
        share = (row["cost"] / s["cost"] * 100) if s["cost"] else 0
        lines.append(f"  {loc}: {row['kwh']:.1f} kWh, ${row['cost']:.2f} "
                     f"({share:.0f}% of the month's cost)")
    return "\n".join(lines)


def split_text(sessions: list[ChargeSession]) -> str:
    if not sessions:
        return "No charging sessions recorded yet."
    s = summarize(sessions)
    lines = [f"All time: {s['sessions']} sessions, {s['kwh']:.1f} kWh, "
             f"${s['cost']:.2f}."]
    for loc, row in sorted(s["by_class"].items()):
        share = (row["cost"] / s["cost"] * 100) if s["cost"] else 0
        lines.append(f"  {loc}: {row['sessions']} sessions, {row['kwh']:.1f} kWh, "
                     f"${row['cost']:.2f} ({share:.0f}% of spend)")
    return "\n".join(lines)


def ledger_brief(sessions: list[ChargeSession]) -> str:
    """Plain-text brief handed to the LLM as context; the rules engine
    answers totals itself, the LLM only explains."""
    if not sessions:
        return "The charging ledger is empty — no sessions recorded yet."
    months = sorted({month_key(s.start) for s in sessions})
    parts = []
    for key in months:
        parts.append(report_text(key, sessions))
    return "\n\n".join(parts)


def session_vector_text(session: ChargeSession) -> str:
    """Text form of a session for /v1/embeddings similarity search:
    similar sessions (same car, place class, size, time of day) embed near
    each other, so 'find nights like last Tuesday' works."""
    hour = session.start.strftime("%I %p").lstrip("0")
    return (f"charging session {session.vehicle} {session.location_class} "
            f"{session.location_name} {session.kwh_added:.0f} kWh "
            f"${(session.cost_usd or 0):.2f} start {hour} "
            f"{session.start_soc:.0f} to {session.end_soc:.0f} percent")


def export_csv(sessions: list[ChargeSession], path: str) -> int:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "vehicle", "start", "end", "start_soc_pct",
                    "end_soc_pct", "kwh_added", "location_class",
                    "location_name", "cost_usd"])
        for s in sorted(sessions, key=lambda x: x.start):
            w.writerow([s.start.strftime("%Y-%m-%d"), s.vehicle,
                        s.start.strftime("%I:%M %p"), s.end.strftime("%I:%M %p"),
                        f"{s.start_soc:.0f}", f"{s.end_soc:.0f}",
                        f"{s.kwh_added:.2f}", s.location_class, s.location_name,
                        f"{(s.cost_usd or 0):.2f}"])
    return len(sessions)
