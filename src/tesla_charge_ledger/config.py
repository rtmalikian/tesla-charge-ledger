"""Configuration for Tesla Charge Ledger. All settings come from the
environment (see .env.example). Copy .env.example to .env — .env is
git-ignored so secrets never leave your machine.

Times are written the way owners say them ("4:00 PM"), never as
24-hour numbers. Electricity prices are time-of-use (TOU) tables in
plain words: name|start|end|price-per-kWh, separated by ';'. The setup
wizard writes this line for you — nobody hand-edits it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


class ConfigError(Exception):
    pass


@dataclass
class VehicleConfig:
    name: str
    vin: str


@dataclass
class TouPeriod:
    name: str
    start_min: int          # minutes after midnight
    end_min: int            # exclusive; may wrap past midnight
    rate_per_kwh: float

    def covers(self, minute: int) -> bool:
        if self.start_min <= self.end_min:
            return self.start_min <= minute < self.end_min
        return minute >= self.start_min or minute < self.end_min


# SCE TOU-D-PRIME-style default (Southern California Edison, the common
# answer for Raphael's home turf): cheap overnight, pricey 4–9 PM.
# Owners on another utility pick their own plan in `tcledger setup`.
DEFAULT_TOU = (
    "off-peak|12:00 AM|4:00 PM|0.28;"
    "peak|4:00 PM|9:00 PM|0.55;"
    "off-peak|9:00 PM|12:00 AM|0.28"
)


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    region: str = "na"
    redirect_uri: str = "http://localhost:8080/callback"
    token_file: str = "~/.tcl/tokens.json"
    vehicles: list[VehicleConfig] = field(default_factory=list)
    # The OpenAI-compatible server this app exposes.
    server_host: str = "127.0.0.1"
    server_port: int = 8648
    server_api_key: str = ""          # optional bearer token for /v1/*
    model_id: str = "tesla-charge-ledger"
    # Pluggable LLM backend (any OpenAI-compatible API; default: local Ollama).
    ai_base_url: str = "http://localhost:11434/v1"
    ai_api_key: str = ""
    ai_model: str = "llama3.1"
    ai_mode: str = "auto"             # auto | always | never
    ai_timeout_seconds: float = 20.0
    embedding_dim: int = 384
    dry_run: bool = False
    # Charging-cost rules (defaults are the common answers; setup writes them).
    tou_rates_raw: str = DEFAULT_TOU
    tou_periods: list[TouPeriod] = field(default_factory=list)
    supercharger_rate: float = 0.45   # $/kWh flat at Superchargers
    public_rate: float = 0.40         # $/kWh flat at other public chargers
    data_file: str = "~/.tcl/ledger.jsonl"


def _parse_vehicles(raw: str) -> list[VehicleConfig]:
    """Format: name=VIN,name2=VIN2 (or bare VINs)."""
    vehicles = []
    for idx, item in enumerate(raw.split(",")):
        item = item.strip()
        if not item:
            continue
        name = None
        if "=" in item:
            name, item = item.split("=", 1)
            name = name.strip()
        vin = item.strip()
        if not vin:
            raise ConfigError("Empty VIN in TCL_VEHICLES")
        vehicles.append(VehicleConfig(name=name or f"car-{idx + 1}", vin=vin))
    if not vehicles:
        raise ConfigError("TCL_VEHICLES is empty — add at least one name=VIN entry")
    return vehicles


def parse_12h_time(raw: str) -> int:
    """'4:00 PM' -> minutes after midnight. Raises ConfigError on bad input."""
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*([APap][Mm])\s*$", raw or "")
    if not m:
        raise ConfigError(
            f"Time must look like '4:00 PM', got {raw!r}. "
            "Write the hour, a colon, the minutes, and AM or PM.")
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if not (1 <= hour <= 12) or not (0 <= minute <= 59):
        raise ConfigError(f"Not a real clock time: {raw!r}")
    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    return hour * 60 + minute


def format_12h_time(minutes: int) -> str:
    minutes %= 24 * 60
    hour, minute = divmod(minutes, 60)
    meridiem = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {meridiem}"


def parse_tou_rates(raw: str) -> list[TouPeriod]:
    """Format: name|start|end|rate; one period per ';'.

    Times are 12-hour words ("4:00 PM"). A period may cross midnight
    (start 9:00 PM, end 12:00 AM). Rates must be positive.
    """
    periods = []
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        if len(parts) != 4:
            raise ConfigError(
                f"Each electricity price window needs name|start|end|price, "
                f"got {item!r} in TCL_TOU_RATES.")
        name, start_s, end_s, rate_s = parts
        try:
            start = parse_12h_time(start_s)
            end = parse_12h_time(end_s)
        except ConfigError as exc:
            raise ConfigError(f"Bad time in price window {item!r}: {exc}") from exc
        try:
            rate = float(rate_s)
        except ValueError as exc:
            raise ConfigError(
                f"Price must be a number (dollars per kWh), got {rate_s!r}") from exc
        if rate <= 0:
            raise ConfigError(f"Price must be above $0, got {rate_s!r}")
        if start == end:
            raise ConfigError(
                f"Price window {item!r} starts and ends at the same time — "
                "a window must cover some part of the day.")
        periods.append(TouPeriod(name=name, start_min=start, end_min=end,
                                 rate_per_kwh=rate))
    if not periods:
        raise ConfigError("TCL_TOU_RATES is empty — add at least one price window")
    return periods


def _int(env: dict, key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from exc


def _float(env: dict, key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _bool(env: dict, key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    s = Settings()
    s.client_id = env.get("TESLA_CLIENT_ID", "")
    s.client_secret = env.get("TESLA_CLIENT_SECRET", "")
    s.region = env.get("TESLA_REGION", "na").strip().lower()
    if s.region not in ("na", "eu", "cn"):
        raise ConfigError(f"TESLA_REGION must be na|eu|cn, got {s.region!r}")
    s.redirect_uri = env.get("TESLA_REDIRECT_URI", s.redirect_uri)
    s.token_file = env.get("TCL_TOKEN_FILE", s.token_file)
    raw_vehicles = env.get("TCL_VEHICLES", "")
    if raw_vehicles.strip():
        s.vehicles = _parse_vehicles(raw_vehicles)
    s.server_host = env.get("TCL_SERVER_HOST", s.server_host)
    s.server_port = _int(env, "TCL_SERVER_PORT", s.server_port)
    s.server_api_key = env.get("TCL_SERVER_API_KEY", "")
    s.model_id = env.get("TCL_MODEL_ID", s.model_id)
    s.ai_base_url = env.get("TCL_AI_BASE_URL", s.ai_base_url).rstrip("/")
    s.ai_api_key = env.get("TCL_AI_API_KEY", "")
    s.ai_model = env.get("TCL_AI_MODEL", s.ai_model)
    s.ai_mode = env.get("TCL_AI_MODE", s.ai_mode).strip().lower()
    if s.ai_mode not in ("auto", "always", "never"):
        raise ConfigError(f"TCL_AI_MODE must be auto|always|never, got {s.ai_mode!r}")
    s.ai_timeout_seconds = _float(env, "TCL_AI_TIMEOUT_SECONDS", s.ai_timeout_seconds)
    s.embedding_dim = _int(env, "TCL_EMBEDDING_DIM", s.embedding_dim)
    if not (8 <= s.embedding_dim <= 4096):
        raise ConfigError("TCL_EMBEDDING_DIM must be between 8 and 4096")
    s.dry_run = _bool(env, "TCL_DRY_RUN")
    s.tou_rates_raw = env.get("TCL_TOU_RATES", s.tou_rates_raw)
    s.tou_periods = parse_tou_rates(s.tou_rates_raw)
    s.supercharger_rate = _float(env, "TCL_SUPERCHARGER_RATE", s.supercharger_rate)
    if s.supercharger_rate <= 0:
        raise ConfigError("TCL_SUPERCHARGER_RATE must be above $0")
    s.public_rate = _float(env, "TCL_PUBLIC_RATE", s.public_rate)
    if s.public_rate <= 0:
        raise ConfigError("TCL_PUBLIC_RATE must be above $0")
    s.data_file = env.get("TCL_DATA_FILE", s.data_file)
    return s
