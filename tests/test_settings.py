import settings as settings_store


def test_load_settings_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    settings = settings_store.load_settings()
    assert settings == settings_store.DEFAULTS


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    saved = settings_store.save_settings({"delete_delay_min": 1.5, "delete_delay_max": 2.5})
    loaded = settings_store.load_settings()
    assert saved["delete_delay_min"] == 1.5
    assert loaded["delete_delay_min"] == 1.5
    assert loaded["delete_delay_max"] == 2.5


def test_values_clamped_to_safe_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    saved = settings_store.save_settings(
        {
            "delete_delay_min": 999,   # above max 10
            "delete_delay_max": 0.1,   # below min 0.5 — swap puts min at the floor
            "max_consecutive_failures": 100000,
        }
    )
    assert saved["delete_delay_min"] == 0.5
    assert saved["delete_delay_max"] == 10.0
    assert saved["max_consecutive_failures"] == 100


def test_min_greater_than_max_swapped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    saved = settings_store.save_settings({"delete_delay_min": 3.0, "delete_delay_max": 1.0})
    assert saved["delete_delay_min"] == 1.0
    assert saved["delete_delay_max"] == 3.0


def test_invalid_values_fall_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    settings_store.SETTINGS_FILE.write_text("not json at all")
    assert settings_store.load_settings() == settings_store.DEFAULTS


def test_bool_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "settings.json")
    saved = settings_store.save_settings({"confirm_before_delete": False})
    assert saved["confirm_before_delete"] is False
    assert settings_store.load_settings()["confirm_before_delete"] is False
