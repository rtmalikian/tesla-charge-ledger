# Tesla Charge Ledger — Free Charging-Cost Ledger with Real Time-of-Use Rates for Tesla Model 3, Model Y, Model S, Model X & Cybertruck

> **▶ Try the live demo: https://rtmalikian.github.io/tesla-charge-ledger/** (interactive, runs on pretend data)

**Free & open source Tesla charging-cost ledger built on the Tesla Fleet API: every charge session priced with your real time-of-use (TOU) electricity rates, monthly + per-car cost reports, a home vs Supercharger cost split, and CSV export — no subscription, no phone-GPS battery drain.**

## Why?

Tesla owners keep asking the same question, and nobody free answers it:

- "The Tesla app shows me kWh, but **never what charging actually cost me in dollars**."
- "Tessie and TezLab charge a subscription just to show a cost estimate."
- "My electricity bill has time-of-use rates — charging at 5 PM costs double what midnight costs — but no Tesla tool prices my sessions that way."
- "Come tax time, I need a clean record of what I spent charging — and where."

Tesla Charge Ledger closes that gap: it records every charge session (start/end battery %, kWh added, home / Supercharger / public) and prices it with **your** TOU rate table, so the number it reports is the number on your bill — not a flat-rate guess.

## How it works

```
Sep 03, 11:00 PM — Model Y at Home: 45 kWh, 30% → 78%, $12.60  (all off-peak)
Sep 07, 03:00 PM — Model Y at Home: 30 kWh, 40% → 72%, $14.47  (3 peak hours!)
Sep 12, 11:30 AM — Model Y at Tejon Ranch Supercharger: 55 kWh, $24.75
...
Charging in September 2026: 7 sessions, 250.0 kWh, $93.82.
  home: 170.0 kWh, $59.07 (63% of the month's cost)
  public: 25.0 kWh, $10.00 (11%)
  supercharger: 55.0 kWh, $24.75 (26%)
```

- **Home sessions** are priced minute-by-minute against your TOU table: energy is spread evenly across the session and each minute is priced at the rate in force then. A session that runs into the 4–9 PM peak costs more than the same energy drawn overnight — exactly like your utility bill.
- **Supercharger / public sessions** are priced at a flat $/kWh you set (Supercharger prices vary by site; override the default any time).
- A session's cost is computed once, at record time, so later rate edits never rewrite history.
- The ledger lives in a local JSONL file on your computer. Nothing about your charging leaves the machine except to an LLM backend **you** configure.

**Expected API cost:** ~$0/month at the default schedule. This app issues no commands and never wakes a car to measure anything — it only prices sessions you record, so there is effectively no Fleet API bill.

## Quickstart (for owners, not developers)

```bash
git clone https://github.com/rtmalikian/tesla-charge-ledger
cd tesla-charge-ledger
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # or just run the wizard:
.venv/bin/python -m tesla_charge_ledger.cli setup   # plain-language Q&A, writes .env for you
.venv/bin/python -m tesla_charge_ledger.cli simulate # see a demo month (no car needed)
.venv/bin/python -m tesla_charge_ledger.cli record   # log your first real session
.venv/bin/python -m tesla_charge_ledger.cli report   # your monthly cost report
```

**No AI needed:** the rules engine answers every cost question with no backend at all. The AI endpoints below are an optional layer that explains the ledger in free-form words.

Commands: `setup` · `auth` · `serve` · `ask "what did charging cost in September?"` · `record` · `report [--month 2026-09]` · `export [--out ledger.csv]` · `preview --kwh 40 --start "5:00 PM" --hours 3` (prices a hypothetical session, saves nothing) · `simulate`.

`record` always shows you the priced session and asks for confirmation before saving; `record --dry-run` previews without saving.

## Tesla developer setup

Create a free app at [developer.tesla.com](https://developer.tesla.com) (sign in → "Create App"), paste the Client ID/Secret into `tcledger setup`, then run `tcledger auth` once and pair the virtual key as described at https://developer.tesla.com/docs/fleet-api#pairing. Required scopes: `vehicle_device_data`, `vehicle_charging_cmds` (the app itself issues no commands — charging data is what the ledger reads).

## AI endpoints (OpenAI-compatible)

`tcledger serve` starts a local server (default http://127.0.0.1:8648) with:

- `GET /v1/models` — the `tesla-charge-ledger` model card.
- `POST /v1/chat/completions` — ask cost questions in plain language; the rules engine answers from the ledger, and an optional LLM backend explains it.
- `POST /v1/embeddings` — deterministic local embeddings (dim 384) over session vectors, so "find nights like last Tuesday" similarity search works with no backend.

```bash
curl http://127.0.0.1:8648/v1/models
curl http://127.0.0.1:8648/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model": "tesla-charge-ledger", "messages": [{"role": "user",
       "content": "what did charging cost in September?"}]}'
```

Pluggable backend: point `TCL_AI_BASE_URL` at local Ollama (`http://localhost:11434/v1`, the default) or any OpenAI-compatible API. `TCL_AI_MODE=auto|always|never`. LLM-emitted content is never executed as commands — this app commands nothing. Vehicle/charging data is sent only to the backend you configure, nowhere else. Optional bearer auth via `TCL_SERVER_API_KEY`. Streaming is not supported (`stream=true` is ignored).

## `.env` reference

| Variable | Meaning | Default |
|---|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | Your developer.tesla.com app credentials | — |
| `TCL_VEHICLES` | `name=VIN` pairs, comma-separated | — |
| `TCL_SERVER_HOST` / `TCL_SERVER_PORT` | The Q&A API address | `127.0.0.1` / `8648` |
| `TCL_SERVER_API_KEY` | Optional bearer token for `/v1/*` | — |
| `TCL_AI_BASE_URL` / `TCL_AI_API_KEY` / `TCL_AI_MODEL` / `TCL_AI_MODE` | Pluggable LLM backend | Ollama / — / `llama3.1` / `auto` |
| `TCL_TOU_RATES` | Home TOU table: `name\|start\|end\|price` per `;` | SCE-style: $0.28 off-peak, $0.55 peak 4–9 PM |
| `TCL_SUPERCHARGER_RATE` / `TCL_PUBLIC_RATE` | Flat $/kWh for public charging | `0.45` / `0.40` |
| `TCL_DATA_FILE` | Ledger file (JSONL) | `~/.tcl/ledger.jsonl` |
| `TCL_DRY_RUN` | `1` = preview, never store | `0` |

## FAQ

**Does it work without a Tesla developer account?**
Yes for everything except live car reads: `setup`, `simulate`, `preview`, and the demo Q&A all run with no credentials. The ledger itself is just priced records — you can log sessions by hand from the car's screen.

**How is this different from Tessie/TezLab?**
Those charge $5–10/month for cost tracking. This is free, open source, local-first, and prices home sessions with your actual time-of-use table instead of a flat guess.

**My utility isn't SCE — can I use my own rates?**
Yes. `tcledger setup` offers a flat-rate plan or your own peak window (hours + prices in plain words), and the TOU table accepts any windows, including overnight ones.

**Does it wake my car or send commands?**
No. It never issues a command or a wake-up. The only Fleet API use is reading charge state while the car is already awake.

**Can I export for taxes?**
Yes: `tcledger export` writes a CSV (date, car, start/end, battery %, kWh, location, cost) shaped like a mileage log — one row per session.

**What if my Supercharger bill differs from the flat rate?**
Edit the session's cost inputs: re-record with your rate, or set `TCL_SUPERCHARGER_RATE` to your area's typical price. The flat rate is a default, not a claim about Tesla's price list.

## License

MIT — Raphael Malikian. Part of the Tesla AI app series (one free Tesla tool a day).
