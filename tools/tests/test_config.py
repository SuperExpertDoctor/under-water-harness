from uuv_game.config import Config


def test_public_model_matches_explicit_worker_model(monkeypatch):
    monkeypatch.setenv("LONGCAT_MODEL", "LongCat-2.5-Preview")
    assert Config().public()["simulation"]["model"] == "LongCat-2.5-Preview"


def test_restored_runtime_reports_current_model(tmp_path, monkeypatch):
    from uuv_game.runtime import MissionRuntime
    path = tmp_path/"model.sqlite"
    instance = MissionRuntime(path)
    instance.close()
    monkeypatch.setenv("LONGCAT_MODEL", "LongCat-2.5-Preview")
    restored = MissionRuntime(path)
    try:
        assert restored.agent["model"] == "LongCat-2.5-Preview"
    finally:
        restored.close()
