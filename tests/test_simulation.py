"""End-to-end: the demo month's reported total equals the sum of its
session costs, and the HTTP server answers a cost question live."""
import threading
from http.server import ThreadingHTTPServer

import requests

from tesla_charge_ledger.config import load_settings
from tesla_charge_ledger.ledger import summarize
from tesla_charge_ledger.server import make_handler
from tesla_charge_ledger.service import ChargeLedgerService
from tesla_charge_ledger.simulator import (DEMO_MONTH, run_demo_month,
                                          timeline_lines)


def _settings():
    return load_settings({"TCL_VEHICLES": "Model Y=VINY",
                          "TCL_AI_MODE": "never"})


def test_demo_month_total_is_sum_of_sessions():
    settings = _settings()
    sessions = run_demo_month(settings)
    assert len(sessions) == 7
    total = summarize([s for s in sessions
                       if s.start.strftime("%Y-%m") == DEMO_MONTH])
    assert total["cost"] == round(sum(s.cost_usd for s in sessions), 2)
    assert total["sessions"] == 7


def test_demo_month_peak_session_costs_more_per_kwh():
    sessions = run_demo_month(_settings())
    by_day = {s.start.day: s for s in sessions}
    cheap = by_day[3].cost_usd / by_day[3].kwh_added
    pricey = by_day[7].cost_usd / by_day[7].kwh_added
    assert pricey > cheap
    assert by_day[27].cost_usd == round(by_day[27].kwh_added * 0.55, 2)


def test_simulate_timeline_mentions_peak_reason():
    lines = timeline_lines(_settings())
    text = "\n".join(lines)
    assert "Sep 07" in text
    assert "peak" in text.lower()
    assert "September 2026" in text


def test_http_server_answers_cost_question():
    settings = _settings()
    sessions = run_demo_month(settings)
    service = ChargeLedgerService(settings, sessions_provider=lambda: sessions)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        models = requests.get(f"http://127.0.0.1:{port}/v1/models", timeout=5)
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "tesla-charge-ledger"
        resp = requests.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={"model": "tesla-charge-ledger",
                  "messages": [{"role": "user",
                                "content": "what did charging cost in September?"}]},
            timeout=5)
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert "September 2026" in content
        assert "$" in content
    finally:
        httpd.shutdown()
        httpd.server_close()
