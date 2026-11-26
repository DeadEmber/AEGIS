"""API end-to-end on a simulated database; control endpoint authorisation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aegis.api.app import create_app
from aegis.config import Settings
from aegis.core.clock import ManualClock
from tests.integration.test_worker_cycle import START, _run, _worker


def _client(tmp_path, token=""):
    clock = ManualClock(START)
    w, _ = _worker(f"sqlite:///{tmp_path}/api.sqlite", clock)
    _run(w, clock, hours=36)
    settings = Settings(database_url=f"sqlite:///{tmp_path}/api.sqlite", api_control_token=token)
    return TestClient(create_app(w.engine, settings, w.cfg, w.limits)), w


def test_all_read_endpoints(tmp_path):
    client, w = _client(tmp_path)
    for path in ("/health", "/overview", "/portfolio/history", "/portfolio/positions", "/portfolio/positions?status=closed",
                 "/discovery/tokens", "/decisions", "/peak-radar", "/second-wave", "/capital", "/risk", "/system",
                 "/shadow", "/intelligence"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert r.json()["data_mode"] == "simulated", path
    ov = client.get("/overview").json()
    assert ov["snapshot"]["reconciliation_ok"] is True
    assert ov["counts"]["buys"] >= 1
    assert isinstance(ov["snapshot"]["net_worth_liquidable_eur"], str)  # exact decimal strings, never floats
    cap = client.get("/capital").json()
    assert cap["reconciliation"]["ok"] and cap["contributions"][0]["amount_eur"] == "100.000000000000"
    pos = client.get("/portfolio/positions").json()["positions"]
    if pos:
        detail = client.get(f"/portfolio/positions/{pos[0]['id']}").json()
        assert detail["lots"] and detail["executions"]
    tok = client.get("/discovery/tokens").json()["tokens"][0]
    assert client.get(f"/tokens/{tok['id']}").status_code == 200
    assert client.get("/tokens/999999").status_code == 404


def test_control_endpoints_require_token(tmp_path):
    client, _ = _client(tmp_path)
    assert client.post("/control/kill-switch", json={"on": True, "reason": "x"}).status_code == 403
    (tmp_path / "b").mkdir()
    client2, _ = _client(tmp_path / "b", token="s3cret")
    assert client2.post("/control/kill-switch", json={"on": True, "reason": "x"},
                        headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client2.post("/control/kill-switch", json={"on": True, "reason": "drill"},
                     headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    flags = {f["key"]: f["value"] for f in client2.get("/risk").json()["flags"]}
    assert flags["kill_switch"] == "on"
