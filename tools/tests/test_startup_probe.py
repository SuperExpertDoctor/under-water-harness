import pytest

from scripts import run
from scripts.run import validate_service_payload


def test_startup_probe_rejects_frontend_html_and_demo_frames():
    with pytest.raises(ValueError, match="health"):
        validate_service_payload({"not": "api"}, {"episode_id": "episode-1"})
    with pytest.raises(ValueError, match="episode"):
        validate_service_payload({"status": "ok", "service": "uuv-runtime"}, {"episode_id": "local-demo"})


def test_startup_probe_accepts_real_episode():
    assert validate_service_payload({"status": "ok", "service": "uuv-runtime"}, {"episode_id": "episode-1"}) == "episode-1"


def test_status_reports_degraded_when_old_frontend_proxy_returns_html(monkeypatch):
    def broken_proxy(_url):
        raise ValueError("/api/health did not return JSON")

    monkeypatch.setattr(run, "probe_frontend", broken_proxy)
    result = run.service_status({"ui_url": "http://127.0.0.1:5186", "backend_url": "http://127.0.0.1:8780"}, True)
    assert result["status"] == "degraded"
    assert "/api/health did not return JSON" in result["error"]
