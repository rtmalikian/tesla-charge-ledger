"""Guided first-run setup: plain-language questions, writes .env itself.

Built for Tesla owners, not developers — defaults everywhere, numbered
choices, electricity prices in plain words ("4:00 PM", "$0.55 per kWh"),
times in 12-hour words. Testable: input and output are injectable.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import DEFAULT_TOU

SCE_TOU = DEFAULT_TOU


def _ask(input_fn, out, prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    out(f"{prompt}{suffix}")
    answer = input_fn("> ").strip()
    return answer or default


def _ask_required(input_fn, out, prompt: str, help_text: str) -> str:
    while True:
        out(help_text)
        answer = input_fn(f"{prompt} ").strip()
        if answer:
            return answer
        out("That one's required — without it the app can't do its job.")


def run_setup(input_fn=input, out=print, env_path: str = ".env") -> Path:
    out("")
    out("Welcome to Tesla Charge Ledger setup — I'll ask a few questions and")
    out("write the settings file for you. Press Enter to accept a [default].")
    out("")

    path = Path(env_path)
    if path.exists():
        again = _ask(input_fn, out,
                     "A settings file (.env) already exists. Replace it? (y/n)", "n")
        if again.lower() not in ("y", "yes"):
            out("Keeping your existing settings. Nothing changed.")
            return path

    out("STEP 1 of 5 — Connect to Tesla")
    out("Create a free developer app at developer.tesla.com (sign in, then")
    out("'Create App'). Tesla will show you a Client ID and Client Secret.")
    client_id = _ask_required(input_fn, out, "Paste your Client ID:",
                              "First, the Client ID:")
    client_secret = _ask_required(input_fn, out, "Paste your Client Secret:",
                                  "Now the Client Secret (it stays on this computer):")

    out("")
    out("STEP 2 of 5 — Your cars")
    out("For each car: a nickname and its VIN. Find the VIN in your Tesla")
    out("app under Controls → Software (or on the car's touchscreen).")
    vehicles: list[tuple[str, str]] = []
    while True:
        n = len(vehicles) + 1
        name = _ask(input_fn, out,
                    f"Car {n} nickname (e.g. Model Y) — or press Enter if done:",
                    "" if vehicles else "")
        if not name:
            if vehicles:
                break
            out("Add at least one car to continue.")
            continue
        vin = _ask_required(input_fn, out, f"VIN for {name}:",
                            f"Now the VIN for {name}:")
        vehicles.append((name, vin))

    out("")
    out("STEP 3 of 5 — What home electricity costs")
    out("Your home rate decides what every home session costs. Pick a plan:")
    out("  1) Time-of-use with a pricey evening peak (SCE-style: cheap")
    out("     overnight, $0.55 per kWh from 4:00 PM to 9:00 PM) — default")
    out("  2) One flat price all day (simpler bill, e.g. $0.30 per kWh)")
    out("  3) Your own peak window (you set the hours and prices)")
    choice = _ask(input_fn, out, "Pick 1, 2 or 3:", "1")
    if choice == "2":
        flat = _ask(input_fn, out,
                    "Your flat price per kWh in dollars (like 0.30):", "0.30")
        tou = f"flat|12:00 AM|12:00 AM|{flat}"  # placeholder, fixed below
        # A flat plan is one window covering the whole day: start=end is
        # rejected by the parser, so encode it as two halves.
        tou = (f"flat|12:00 AM|12:00 PM|{flat};"
               f"flat|12:00 PM|12:00 AM|{flat}")
    elif choice == "3":
        peak_start = _ask(input_fn, out,
                          "When does your expensive peak start (like 4:00 PM)?",
                          "4:00 PM")
        peak_end = _ask(input_fn, out,
                        "When does it end (like 9:00 PM)?", "9:00 PM")
        peak_rate = _ask(input_fn, out,
                         "Peak price per kWh in dollars (like 0.55):", "0.55")
        off_rate = _ask(input_fn, out,
                        "Price the rest of the day (like 0.28):", "0.28")
        tou = (f"off-peak|12:00 AM|{peak_start}|{off_rate};"
               f"peak|{peak_start}|{peak_end}|{peak_rate};"
               f"off-peak|{peak_end}|12:00 AM|{off_rate}")
    else:
        tou = SCE_TOU

    out("")
    out("STEP 4 of 5 — What public charging costs")
    out("Supercharger and public-charger prices vary by site, so these are")
    out("flat defaults you can override per session later.")
    sc_rate = _ask(input_fn, out,
                   "Supercharger price per kWh in dollars:", "0.45")
    pub_rate = _ask(input_fn, out,
                    "Other public charger price per kWh in dollars:", "0.40")

    out("")
    out("STEP 5 of 5 — The AI brain (optional) and the report server")
    out("  1) Built-in answers only — no AI, nothing leaves this computer")
    out("  2) Ollama on this computer — free local AI (default)")
    out("  3) Another AI service (OpenAI, Groq, LM Studio…) — you'll paste its details")
    choice = _ask(input_fn, out, "Pick 1, 2 or 3:", "2")
    ai_mode, ai_url, ai_key, ai_model = "auto", "http://localhost:11434/v1", "", "llama3.1"
    if choice == "1":
        ai_mode = "never"
    elif choice == "3":
        ai_url = _ask_required(input_fn, out, "Service address (base URL):",
                               "Paste the service's API address:")
        ai_key = _ask(input_fn, out, "API key for that service (stored only here):")
        ai_model = _ask(input_fn, out, "Model name:", "gpt-4o-mini")
    port = _ask(input_fn, out, "Port for the report address:", "8648")
    server_key = _ask(input_fn, out,
                      "Optional password for the report address (blank = none):")

    lines = [
        "# Written by `tcledger setup` — edit by re-running setup any time.",
        f"TESLA_CLIENT_ID={client_id}",
        f"TESLA_CLIENT_SECRET={client_secret}",
        "TESLA_REGION=na",
        "TESLA_REDIRECT_URI=http://localhost:8080/callback",
        "TCL_VEHICLES=" + ",".join(f"{name}={vin}" for name, vin in vehicles),
        "TCL_SERVER_HOST=127.0.0.1",
        f"TCL_SERVER_PORT={port}",
        f"TCL_SERVER_API_KEY={server_key}",
        "TCL_MODEL_ID=tesla-charge-ledger",
        f"TCL_AI_BASE_URL={ai_url}",
        f"TCL_AI_API_KEY={ai_key}",
        f"TCL_AI_MODEL={ai_model}",
        f"TCL_AI_MODE={ai_mode}",
        "TCL_AI_TIMEOUT_SECONDS=20",
        "TCL_EMBEDDING_DIM=384",
        "TCL_TOKEN_FILE=~/.tcl/tokens.json",
        f"TCL_TOU_RATES={tou}",
        f"TCL_SUPERCHARGER_RATE={sc_rate}",
        f"TCL_PUBLIC_RATE={pub_rate}",
        "TCL_DATA_FILE=~/.tcl/ledger.jsonl",
        "TCL_DRY_RUN=0",
    ]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)

    out("")
    out("All set — settings saved to .env (only readable by you).")
    out("Next steps:")
    out("  1) tcledger auth       — sign in to Tesla once (opens a link)")
    out("  2) tcledger simulate   — see a demo month with pretend sessions")
    out("  3) tcledger record     — log your first real charging session")
    out("  4) tcledger report     — your monthly charging-cost report")
    out("  5) tcledger serve      — start the cost Q&A API")
    return path
