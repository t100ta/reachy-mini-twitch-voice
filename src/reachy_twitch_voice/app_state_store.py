from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class AppStateStore:
    """Persists runtime secrets (secrets.json, 0600) and non-secret settings (settings.json)."""

    def __init__(self, config_dir: str | Path) -> None:
        self.root = Path(config_dir).expanduser()

    def _ensure_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # --- secrets.json (0600) ---
    @property
    def _secrets_path(self) -> Path:
        return self.root / "secrets.json"

    def load_token(self) -> str | None:
        try:
            text = self._secrets_path.read_text(encoding="utf-8")
            data = json.loads(text)
            return data.get("twitch_oauth_token") or None
        except FileNotFoundError:
            return None
        except Exception as exc:
            LOGGER.warning("app_state_store: failed to load secrets.json: %s", exc)
            return None

    def save_token(self, token: str) -> None:
        self._ensure_dir()
        path = self._secrets_path
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            existing = {}
        existing["twitch_oauth_token"] = token
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)

    # --- settings.json (normal permissions) ---
    @property
    def _settings_path(self) -> Path:
        return self.root / "settings.json"

    def load_settings(self) -> dict:
        try:
            text = self._settings_path.read_text(encoding="utf-8")
            return json.loads(text)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            LOGGER.warning("app_state_store: failed to load settings.json: %s", exc)
            return {}

    def save_setting(self, key: str, value: object) -> None:
        self._ensure_dir()
        path = self._settings_path
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            existing = {}
        existing[key] = value
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
