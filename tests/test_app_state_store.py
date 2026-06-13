from __future__ import annotations

import os

import pytest

from reachy_twitch_voice.app_state_store import AppStateStore


def test_token_roundtrip(tmp_path):
    store = AppStateStore(tmp_path / "config")
    assert store.load_token() is None
    store.save_token("oauth:testtoken123")
    assert store.load_token() == "oauth:testtoken123"


def test_secrets_file_permissions(tmp_path):
    store = AppStateStore(tmp_path / "config")
    store.save_token("oauth:abc")
    path = store._secrets_path
    mode = oct(os.stat(path).st_mode)[-3:]
    assert mode == "600", f"Expected 600, got {mode}"


def test_settings_roundtrip(tmp_path):
    store = AppStateStore(tmp_path / "config")
    assert store.load_settings() == {}
    store.save_setting("tts_voice", "nova")
    assert store.load_settings()["tts_voice"] == "nova"


def test_save_merges_existing(tmp_path):
    store = AppStateStore(tmp_path / "config")
    store.save_setting("tts_voice", "alloy")
    store.save_setting("other_key", "value")
    settings = store.load_settings()
    assert settings["tts_voice"] == "alloy"
    assert settings["other_key"] == "value"


def test_token_overwrite(tmp_path):
    store = AppStateStore(tmp_path / "config")
    store.save_token("oauth:first")
    store.save_token("oauth:second")
    assert store.load_token() == "oauth:second"


def test_save_token_preserves_other_secrets(tmp_path):
    store = AppStateStore(tmp_path / "config")
    # Manually write a secrets.json with extra keys
    import json
    store._ensure_dir()
    store._secrets_path.write_text(json.dumps({"other_secret": "keep_me"}), encoding="utf-8")
    os.chmod(store._secrets_path, 0o600)
    store.save_token("oauth:abc")
    data = json.loads(store._secrets_path.read_text(encoding="utf-8"))
    assert data["other_secret"] == "keep_me"
    assert data["twitch_oauth_token"] == "oauth:abc"
