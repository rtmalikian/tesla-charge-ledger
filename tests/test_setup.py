"""The setup wizard must produce a config the app can load — no developer
knowledge required from the (scripted) user."""
from tesla_charge_ledger.config import load_settings
from tesla_charge_ledger.setup import run_setup


def _load_env_text(raw: str) -> dict:
    env = {}
    for line in raw.splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def test_setup_writes_loadable_config(tmp_path):
    answers = iter([
        "cid-123", "csecret-456",          # Tesla app credentials
        "Model Y", "VINY", "",             # cars (blank = done)
        "1",                               # rate plan: SCE TOU preset
        "0.45", "0.40",                    # Supercharger / public rates
        "1",                               # AI: built-in only
        "8648", "",                        # server port, no password
    ])
    out_lines = []
    env_path = tmp_path / ".env"
    run_setup(input_fn=lambda prompt="": next(answers),
              out=out_lines.append, env_path=str(env_path))
    assert env_path.exists()
    raw = env_path.read_text()
    assert "TCL_VEHICLES=Model Y=VINY" in raw
    assert "TCL_SUPERCHARGER_RATE=0.45" in raw
    assert "TCL_AI_MODE=never" in raw
    assert "TCL_SERVER_PORT=8648" in raw
    s = load_settings(_load_env_text(raw))
    assert s.vehicles[0].name == "Model Y"
    assert s.supercharger_rate == 0.45
    assert len(s.tou_periods) == 3


def test_setup_flat_rate_plan_loads(tmp_path):
    answers = iter([
        "cid-123", "csecret-456",
        "Model 3", "VIN3", "",
        "2", "0.30",                      # flat plan at $0.30/kWh
        "0.50", "0.35",
        "2",                               # AI: Ollama default
        "8648", "",
    ])
    env_path = tmp_path / ".env"
    run_setup(input_fn=lambda prompt="": next(answers),
              out=lambda *a: None, env_path=str(env_path))
    s = load_settings(_load_env_text(env_path.read_text()))
    assert all(p.rate_per_kwh == 0.30 for p in s.tou_periods)
    assert s.ai_mode == "auto"


def test_setup_custom_peak_window_loads(tmp_path):
    answers = iter([
        "cid-123", "csecret-456",
        "Model Y", "VINY", "",
        "3", "5:00 PM", "10:00 PM", "0.60", "0.25",  # custom peak
        "0.45", "0.40",
        "1",
        "8648", "",
    ])
    env_path = tmp_path / ".env"
    run_setup(input_fn=lambda prompt="": next(answers),
              out=lambda *a: None, env_path=str(env_path))
    s = load_settings(_load_env_text(env_path.read_text()))
    peak = [p for p in s.tou_periods if p.name == "peak"][0]
    assert peak.rate_per_kwh == 0.60
    assert peak.start_min == 17 * 60


def test_setup_keeps_existing_config(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TCL_VEHICLES=Model Y=VINY\n")
    answers = iter(["n"])
    run_setup(input_fn=lambda prompt="": next(answers), out=lambda *a: None,
              env_path=str(env_path))
    assert env_path.read_text() == "TCL_VEHICLES=Model Y=VINY\n"
