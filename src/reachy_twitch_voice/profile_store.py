from __future__ import annotations

import importlib.resources as resources
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ConversationConfig


ACTIVE_PROFILE_FILE = ".active_profile"
PROFILE_META_FILE = "profile.json"
PROFILE_PROMPT_FILE = "instructions.txt"


@dataclass(slots=True)
class ProfileData:
    name: str
    persona_name: str
    persona_name_kana: str
    operator_name: str
    persona_style: str
    operator_usernames: list[str]
    system_prompt_text: str


class ProfileStore:
    def __init__(self, storage_dir: str, default_cfg: ConversationConfig) -> None:
        self.root = Path(storage_dir).expanduser()
        self.default_cfg = default_cfg

    def ensure_storage(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[str]:
        if not self.root.exists():
            return []
        names: list[str] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            if (path / PROFILE_META_FILE).exists() and (path / PROFILE_PROMPT_FILE).exists():
                names.append(path.name)
        return names

    def get_active_profile(self) -> str:
        path = self.root / ACTIVE_PROFILE_FILE
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def set_active_profile(self, name: str) -> None:
        self.ensure_storage()
        (self.root / ACTIVE_PROFILE_FILE).write_text(f"{name.strip()}\n", encoding="utf-8")

    def resolve_active_profile_name(self, requested_name: str = "") -> str:
        if requested_name:
            return requested_name
        return self.get_active_profile()

    def load_profile(self, name: str) -> ProfileData:
        profile_dir = self.root / name
        meta = json.loads((profile_dir / PROFILE_META_FILE).read_text(encoding="utf-8"))
        prompt = (profile_dir / PROFILE_PROMPT_FILE).read_text(encoding="utf-8")
        usernames = meta.get("operator_usernames", [])
        if not isinstance(usernames, list):
            usernames = []
        return ProfileData(
            name=name,
            persona_name=str(meta.get("persona_name", self.default_cfg.persona_name)),
            persona_name_kana=str(
                meta.get("persona_name_kana", self.default_cfg.persona_name_kana)
            ),
            operator_name=str(meta.get("operator_name", self.default_cfg.operator_name)),
            persona_style=str(meta.get("persona_style", self.default_cfg.persona_style)),
            operator_usernames=[str(v).strip().lower() for v in usernames if str(v).strip()],
            system_prompt_text=prompt,
        )

    def save_profile(self, data: ProfileData) -> str:
        self.ensure_storage()
        safe_name = self.sanitize_name(data.name)
        profile_dir = self.root / safe_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "persona_name": data.persona_name,
            "persona_name_kana": data.persona_name_kana,
            "operator_name": data.operator_name,
            "persona_style": data.persona_style,
            "operator_usernames": [u.strip().lower() for u in data.operator_usernames if u.strip()],
        }
        (profile_dir / PROFILE_META_FILE).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        prompt = data.system_prompt_text.rstrip() + "\n"
        (profile_dir / PROFILE_PROMPT_FILE).write_text(prompt, encoding="utf-8")
        return safe_name

    def build_default_profile(self) -> ProfileData:
        return ProfileData(
            name="default",
            persona_name=self.default_cfg.persona_name,
            persona_name_kana=self.default_cfg.persona_name_kana,
            operator_name=self.default_cfg.operator_name,
            persona_style=self.default_cfg.persona_style,
            operator_usernames=list(self.default_cfg.operator_usernames),
            system_prompt_text=self._load_default_prompt_text(),
        )

    def apply_profile_to_config(
        self,
        cfg: ConversationConfig,
        profile: ProfileData,
    ) -> ConversationConfig:
        return ConversationConfig(
            engine=cfg.engine,
            input_mode=cfg.input_mode,
            context_window_size=cfg.context_window_size,
            openai_api_key=cfg.openai_api_key,
            openai_realtime_model=cfg.openai_realtime_model,
            openai_timeout_sec=cfg.openai_timeout_sec,
            persona_name=profile.persona_name,
            persona_name_kana=profile.persona_name_kana,
            operator_name=profile.operator_name,
            persona_style=profile.persona_style,
            system_prompt_file="",
            system_prompt_text=profile.system_prompt_text,
            operator_usernames=list(profile.operator_usernames),
            profile_storage_dir=cfg.profile_storage_dir,
            active_profile=profile.name,
        )

    def _load_default_prompt_text(self) -> str:
        if self.default_cfg.system_prompt_text.strip():
            return self.default_cfg.system_prompt_text
        if self.default_cfg.system_prompt_file.strip():
            try:
                return Path(self.default_cfg.system_prompt_file).read_text(encoding="utf-8")
            except OSError:
                pass
        try:
            return (
                resources.files("reachy_twitch_voice.prompts")
                .joinpath("system_ja.txt")
                .read_text(encoding="utf-8")
            )
        except OSError:
            return ""

    @staticmethod
    def sanitize_name(name: str) -> str:
        cleaned = re.sub(r"\s+", "_", name.strip())
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", cleaned)
        return cleaned or "profile"
