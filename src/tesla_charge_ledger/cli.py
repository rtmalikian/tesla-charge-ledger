"""CLI: tcledger setup | auth | serve | ask | record | report | export | preview | simulate

Safety posture: recording a session only writes to a file on your own
computer — nothing is sent anywhere, and no car is ever commanded.
`record --dry-run` and `preview` show the cost before anything is stored.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from .config import ConfigError, load_settings, parse_12h_time

__version__ = "0.1.0"


def _setup_logging(verbose: bool) -> None:
    import logging
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")


def _client(settings):
    from .tesla_client import TeslaClient
    return TeslaClient(settings.client_id, settings.client_secret,
                       region=settings.region, redirect_uri=settings.redirect_uri,
                       token_file=settings.token_file)


def cmd_auth(args, settings) -> int:
    client = _client(settings)
    print("Open this URL in your browser and authorize:")
    print(client.authorize_url())
    print("Virtual-key pairing: https://developer.tesla.com/docs/fleet-api#pairing")
    return 0


def cmd_setup(args, settings) -> int:
    from .setup import run_setup
    run_setup()
    return 0


def _sessions_for_api(settings):
    """Real ledger if it exists, else the demo month (labelled as such)
    so the API is testable before the first real session."""
    from .ledger import load_sessions
    from .simulator import run_demo_month
    real = load_sessions(settings.data_file)
    if real:
        return real, False
    return run_demo_month(settings), True


def cmd_serve(args, settings) -> int:
    from .server import serve
    from .service import ChargeLedgerService
    sessions, is_demo = _sessions_for_api(settings)
    service = ChargeLedgerService(settings, sessions_provider=lambda: sessions)
    print("Ledger data goes ONLY to your configured LLM backend "
          f"(TCL_AI_BASE_URL={settings.ai_base_url or 'none'}); "
          "the rules engine answers cost questions with no backend at all.")
    if is_demo:
        print("(Serving the demo month — record a real session and the")
        print(" API will answer from your ledger instead.)")
    print("This app only records and prices charging — it never commands a car.")
    serve(service)
    return 0


def cmd_ask(args, settings) -> int:
    from .service import ChargeLedgerService
    sessions, is_demo = _sessions_for_api(settings)
    service = ChargeLedgerService(settings, sessions_provider=lambda: sessions)
    if is_demo:
        print("(Demo month — record a real session to ask about your ledger.)")
    body = {"model": settings.model_id,
            "messages": [{"role": "user", "content": args.question}]}
    resp = service.chat_completions(body)
    print(resp["choices"][0]["message"]["content"])
    return 0


def _parse_session_args(args, settings):
    """Build a ChargeSession from CLI args, or ask interactively for
    anything missing. Returns (session, priced_preview_bool)."""
    from .ledger import ChargeSession, price_session
    cars = [v.name for v in settings.vehicles]
    car = args.car
    if not car:
        if len(cars) == 1:
            car = cars[0]
            print(f"Car: {car} (your only car — run setup to add more)")
        elif cars:
            print("Which car charged?")
            for i, name in enumerate(cars, 1):
                print(f"  {i}) {name}")
            pick = input("> ").strip()
            try:
                car = cars[int(pick) - 1]
            except (ValueError, IndexError):
                print("That wasn't one of the numbered cars — try again.")
                return None
        else:
            car = input("Car nickname (e.g. Model Y): ").strip() or "Model Y"

    def ask(prompt, default=""):
        suffix = f" [{default}]" if default else ""
        answer = input(f"{prompt}{suffix}\n> ").strip()
        return answer or default

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_s = args.date or ask("Date of the session (like 2026-09-15)", today)
    start_s = args.start or ask("Charging started at (like 11:00 PM)", "11:00 PM")
    end_s = args.end or ask("Charging ended at (like 6:00 AM)", "6:00 AM")
    try:
        day = datetime.strptime(date_s, "%Y-%m-%d")
    except ValueError:
        print(f"Couldn't read that date: {date_s!r}. Write it like 2026-09-15.")
        return None
    try:
        start_min = parse_12h_time(start_s)
        end_min = parse_12h_time(end_s)
    except ConfigError as exc:
        print(f"Couldn't read that time: {exc}")
        return None
    start = day.replace(hour=start_min // 60, minute=start_min % 60,
                        tzinfo=timezone.utc)
    end = day.replace(hour=end_min // 60, minute=end_min % 60,
                      tzinfo=timezone.utc)
    if end <= start:  # overnight session: end is the next morning
        end += timedelta(days=1)
    try:
        start_soc = float(args.start_soc if args.start_soc is not None
                          else ask("Battery % when charging started:", "30"))
        end_soc = float(args.end_soc if args.end_soc is not None
                        else ask("Battery % when it finished:", "80"))
        kwh = float(args.kwh if args.kwh is not None
                    else ask("Energy added in kWh (from the car's screen):", "40"))
    except ValueError:
        print("Those need to be numbers — battery % like 30, kWh like 40.")
        return None
    loc = args.location_class
    if not loc:
        print("Where did you charge?")
        print("  1) Home")
        print("  2) Supercharger")
        print("  3) Other public charger")
        pick = ask("Pick 1, 2 or 3:", "1")
        loc = {"1": "home", "2": "supercharger", "3": "public"}.get(pick, "home")
    place = args.place if args.place is not None else ask(
        "Charger name or place (blank is fine):", "")
    session = ChargeSession(car, start, end, start_soc, end_soc, kwh, loc, place)
    try:
        price_session(session, settings.tou_periods,
                      settings.supercharger_rate, settings.public_rate)
    except Exception as exc:
        print(f"That session doesn't add up: {exc}")
        return None
    return session


def cmd_record(args, settings) -> int:
    from .ledger import append_session
    session = _parse_session_args(args, settings)
    if session is None:
        return 2
    print("")
    print("Here's what I would record:")
    print(f"  {session.vehicle} at {session.location_name or session.location_class}, "
          f"{session.start.strftime('%b %d, %I:%M %p')} → "
          f"{session.end.strftime('%I:%M %p')}, {session.kwh_added:.1f} kWh, "
          f"${(session.cost_usd or 0):.2f}")
    if args.dry_run or settings.dry_run:
        print("Dry run — nothing was saved. Re-run without --dry-run to save it.")
        return 0
    if not args.yes:
        answer = input("Save this session? (y/n) ").strip().lower()
        if answer not in ("y", "yes"):
            print("Not saved.")
            return 0
    append_session(settings.data_file, session)
    print(f"Saved to {settings.data_file}.")
    return 0


def cmd_report(args, settings) -> int:
    from .ledger import (filter_month, load_sessions, month_key, parse_month_query,
                         report_text)
    sessions = load_sessions(settings.data_file)
    if not sessions:
        print("Your ledger is empty — nothing to report yet.")
        print("Record a session with `tcledger record`, or see a demo month")
        print("with `tcledger simulate`.")
        return 0
    if args.month:
        key = parse_month_query(args.month, sessions)
        if key is None:
            print(f"Couldn't read that month: {args.month!r}. Try 2026-09 or "
                  "'September'.")
            return 2
    else:
        key = month_key(max(s.start for s in sessions))
    if args.car:
        sessions = [s for s in sessions if s.vehicle.lower() == args.car.lower()]
    print(report_text(key, sessions))
    return 0


def cmd_export(args, settings) -> int:
    from .ledger import export_csv, load_sessions
    sessions = load_sessions(settings.data_file)
    if not sessions:
        print("Your ledger is empty — nothing to export yet.")
        return 0
    out = args.out or "charge-ledger.csv"
    n = export_csv(sessions, out)
    print(f"Wrote {n} sessions to {out} (date, purpose, start/end, miles-style "
          "columns shaped like a mileage log).")
    return 0


def cmd_preview(args, settings) -> int:
    """Price a hypothetical session without storing anything."""
    from .ledger import ChargeSession, price_session
    try:
        kwh = float(args.kwh)
        start_min = parse_12h_time(args.start)
        hours = float(args.hours)
    except (ValueError, ConfigError) as exc:
        print(f"Couldn't read that: {exc}")
        return 2
    day = datetime.now(timezone.utc).replace(hour=start_min // 60,
                                             minute=start_min % 60)
    end = day + timedelta(hours=hours)
    session = ChargeSession("Preview car", day, end, 30, 80, kwh,
                            args.location_class, args.place or "")
    price_session(session, settings.tou_periods,
                  settings.supercharger_rate, settings.public_rate)
    print(f"Preview: {kwh:.0f} kWh starting {args.start} for {hours:g} hours "
          f"at {args.location_class} would cost ${(session.cost_usd or 0):.2f}.")
    if session.cost_breakdown:
        for period, (pkwh, pcost) in session.cost_breakdown.items():
            print(f"  {period}: {pkwh:.1f} kWh → ${pcost:.2f}")
    print("Nothing was saved — this was a preview only.")
    return 0


def cmd_simulate(_args, settings) -> int:
    from .simulator import timeline_lines
    for line in timeline_lines(settings):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tcledger",
        description="A free charging-cost ledger with real time-of-use rates "
                    "for your Teslas.")
    p.add_argument("--version", action="version", version=f"tcledger {__version__}")
    p.add_argument("--env-file", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Guided first-run setup (plain-language questions)")
    sub.add_parser("auth", help="Authorize with your Tesla account (one-time)")
    sub.add_parser("serve", help="Run the OpenAI-compatible cost Q&A API server")
    ask_p = sub.add_parser("ask", help="One-shot cost question (your ledger, or demo month)")
    ask_p.add_argument("question", help="e.g. 'what did charging cost in September?'")
    rec_p = sub.add_parser("record", help="Log a charging session (asks what it needs)")
    rec_p.add_argument("--car", default=None)
    rec_p.add_argument("--date", default=None, help="YYYY-MM-DD")
    rec_p.add_argument("--start", default=None, help="e.g. 11:00 PM")
    rec_p.add_argument("--end", default=None, help="e.g. 6:00 AM")
    rec_p.add_argument("--start-soc", type=float, default=None)
    rec_p.add_argument("--end-soc", type=float, default=None)
    rec_p.add_argument("--kwh", type=float, default=None)
    rec_p.add_argument("--location-class", default=None,
                       choices=["home", "supercharger", "public"])
    rec_p.add_argument("--place", default=None)
    rec_p.add_argument("--dry-run", action="store_true",
                       help="Show what would be recorded, save nothing")
    rec_p.add_argument("--yes", action="store_true",
                       help="Save without asking for confirmation")
    rep_p = sub.add_parser("report", help="Monthly charging-cost report")
    rep_p.add_argument("--month", default=None, help="e.g. 2026-09 or September")
    rep_p.add_argument("--car", default=None)
    exp_p = sub.add_parser("export", help="Export the ledger to CSV")
    exp_p.add_argument("--out", default=None)
    pre_p = sub.add_parser("preview", help="Price a hypothetical session (saves nothing)")
    pre_p.add_argument("--kwh", required=True)
    pre_p.add_argument("--start", default="11:00 PM")
    pre_p.add_argument("--hours", default="7")
    pre_p.add_argument("--location-class", default="home",
                       choices=["home", "supercharger", "public"])
    pre_p.add_argument("--place", default=None)
    sub.add_parser("simulate", help="Demo month timeline (no API calls)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    env = dict(os.environ)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
        env = dict(os.environ)
    else:
        from dotenv import load_dotenv
        load_dotenv(override=False)
        env = dict(os.environ)
    try:
        settings = load_settings(env)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    handlers = {"setup": cmd_setup, "auth": cmd_auth, "serve": cmd_serve,
                "ask": cmd_ask, "record": cmd_record, "report": cmd_report,
                "export": cmd_export, "preview": cmd_preview,
                "simulate": cmd_simulate}
    return handlers[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
