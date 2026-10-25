"""OpenAI response shapes + ledger Q&A rules. The rules engine must
answer cost questions with ledger totals, never invented numbers."""
from tesla_charge_ledger.config import load_settings
from tesla_charge_ledger.service import ChargeLedgerService
from tesla_charge_ledger.simulator import run_demo_month


def _service(sessions=None):
    settings = load_settings({"TCL_VEHICLES": "Model Y=VINY",
                              "TCL_AI_MODE": "never"})
    if sessions is None:
        sessions = run_demo_month(settings)
    return ChargeLedgerService(settings, sessions_provider=lambda: sessions)


def _ask(service, question):
    resp = service.chat_completions(
        {"model": "tesla-charge-ledger",
         "messages": [{"role": "user", "content": question}]})
    assert resp["object"] == "chat.completion"
    assert resp["model"] == "tesla-charge-ledger"
    assert resp["choices"][0]["message"]["role"] == "assistant"
    assert resp["choices"][0]["finish_reason"] == "stop"
    return resp["choices"][0]["message"]["content"]


def test_models_shape():
    models = _service().models_list()
    assert models["object"] == "list"
    assert models["data"][0]["id"] == "tesla-charge-ledger"


def test_september_cost_answer_matches_ledger_math():
    service = _service()
    sessions = run_demo_month(load_settings({"TCL_AI_MODE": "never"}))
    total = sum(s.cost_usd for s in sessions)
    answer = _ask(service, "what did charging cost in September?")
    assert f"${total:.2f}" in answer
    assert "September 2026" in answer


def test_split_answer_and_per_car_filter():
    service = _service()
    split = _ask(service, "home vs Supercharger split?")
    assert "home" in split and "supercharger" in split
    per_car = _ask(service, "what did the Model Y charging cost in September?")
    assert "Model Y" in per_car


def test_total_and_extremes():
    service = _service()
    total = _ask(service, "how much have I spent in total?")
    assert "$" in total
    priciest = _ask(service, "what was my most expensive session?")
    assert "priciest session" in priciest
    cheapest = _ask(service, "what was my cheapest session?")
    assert "cheapest session" in cheapest


def test_unknown_question_falls_back_to_capability_blurb():
    service = _service()
    answer = _ask(service, "tell me a joke about the moon")
    assert "charging-cost ledger" in answer


def test_embeddings_shape_and_determinism():
    service = _service()
    resp = service.embeddings({"input": "charging session Model Y home 45 kWh"})
    assert resp["object"] == "list"
    assert len(resp["data"]) == 1
    assert len(resp["data"][0]["embedding"]) == 384
    again = service.embeddings({"input": "charging session Model Y home 45 kWh"})
    assert again["data"][0]["embedding"] == resp["data"][0]["embedding"]
    batch = service.embeddings({"input": ["home charging", "supercharger stop"]})
    assert len(batch["data"]) == 2


def test_empty_ledger_falls_back_gracefully():
    service = _service(sessions=[])
    answer = _ask(service, "what did charging cost in September?")
    assert "charging-cost ledger" in answer
