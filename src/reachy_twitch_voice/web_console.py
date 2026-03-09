from __future__ import annotations

import asyncio
import logging
from typing import Any

from .orchestrator import AppOrchestrator
from .profile_store import ProfileData, ProfileStore

LOGGER = logging.getLogger(__name__)


class WebConsoleServer:
    def __init__(
        self,
        app: AppOrchestrator,
        store: ProfileStore,
        loop: asyncio.AbstractEventLoop,
        host: str,
        port: int,
    ) -> None:
        self.app = app
        self.store = store
        self.loop = loop
        self.host = host
        self.port = port
        self._ui: Any | None = None
        self._gradio: Any | None = None

    def start(self) -> None:
        try:
            import gradio as gr
        except Exception as exc:
            LOGGER.warning("Web console disabled: gradio import failed: %s", exc)
            return

        self._gradio = gr
        current = self._current_profile_data()
        profiles = self._profile_choices()

        with gr.Blocks(title="Reachy Mini Twitch Voice Console") as ui:
            gr.Markdown(
                (
                    "# Reachy Mini Twitch Voice Console\n"
                    "LAN公開・無認証です。同一LAN内でのみ利用してください。"
                )
            )
            profile_name = gr.Textbox(label="Profile Name", value=current.name)
            profile_select = gr.Dropdown(
                label="Saved Profiles",
                choices=profiles,
                value=current.name if current.name in profiles else None,
                allow_custom_value=True,
            )
            persona_name = gr.Textbox(label="Persona Name", value=current.persona_name)
            persona_name_kana = gr.Textbox(
                label="Persona Name Kana",
                value=current.persona_name_kana,
            )
            operator_name = gr.Textbox(label="Operator Name", value=current.operator_name)
            persona_style = gr.Textbox(label="Persona Style", value=current.persona_style)
            operator_usernames = gr.Textbox(
                label="Operator Usernames",
                value=",".join(current.operator_usernames),
            )
            prompt_text = gr.TextArea(
                label="System Prompt",
                value=current.system_prompt_text,
                lines=14,
            )
            preview = gr.TextArea(
                label="Profile Preview",
                value=current.system_prompt_text,
                lines=10,
                interactive=False,
            )
            status = gr.Markdown("")
            save_btn = gr.Button("Save")
            apply_btn = gr.Button("Apply")
            reload_btn = gr.Button("Load")

            def _load(name: str) -> tuple[str, str, str, str, str, str, str, dict[str, Any], str]:
                data = self._load_profile_for_ui(name)
                return (
                    data.name,
                    data.persona_name,
                    data.persona_name_kana,
                    data.operator_name,
                    data.persona_style,
                    ",".join(data.operator_usernames),
                    data.system_prompt_text,
                    gr.update(choices=self._profile_choices(), value=data.name),
                    data.system_prompt_text,
                )

            def _save(
                name: str,
                p_name: str,
                p_kana: str,
                op_name: str,
                p_style: str,
                op_users: str,
                prompt: str,
            ) -> tuple[dict[str, Any], str, str]:
                data = self._profile_data_from_ui(
                    name,
                    p_name,
                    p_kana,
                    op_name,
                    p_style,
                    op_users,
                    prompt,
                )
                saved = self.store.save_profile(data)
                return (
                    gr.update(choices=self._profile_choices(), value=saved),
                    f"Saved profile `{saved}`.",
                    prompt,
                )

            def _apply(
                selected_name: str,
                p_name: str,
                p_kana: str,
                op_name: str,
                p_style: str,
                op_users: str,
                prompt: str,
            ) -> tuple[str, dict[str, Any], str]:
                data = self._profile_data_from_ui(
                    selected_name,
                    p_name,
                    p_kana,
                    op_name,
                    p_style,
                    op_users,
                    prompt,
                )
                saved = self.store.save_profile(data)
                self.store.set_active_profile(saved)
                applied = self._apply_profile(saved)
                return (
                    applied,
                    gr.update(choices=self._profile_choices(), value=saved),
                    prompt,
                )

            reload_btn.click(
                fn=_load,
                inputs=[profile_select],
                outputs=[
                    profile_name,
                    persona_name,
                    persona_name_kana,
                    operator_name,
                    persona_style,
                    operator_usernames,
                    prompt_text,
                    profile_select,
                    preview,
                ],
            )
            save_btn.click(
                fn=_save,
                inputs=[
                    profile_name,
                    persona_name,
                    persona_name_kana,
                    operator_name,
                    persona_style,
                    operator_usernames,
                    prompt_text,
                ],
                outputs=[profile_select, status, preview],
            )
            apply_btn.click(
                fn=_apply,
                inputs=[
                    profile_name,
                    persona_name,
                    persona_name_kana,
                    operator_name,
                    persona_style,
                    operator_usernames,
                    prompt_text,
                ],
                outputs=[status, profile_select, preview],
            )

        self._ui = ui
        try:
            ui.launch(
                server_name=self.host,
                server_port=self.port,
                prevent_thread_lock=True,
                quiet=True,
                show_api=False,
            )
        except Exception as exc:
            LOGGER.warning("Web console failed to start: %s", exc)
            self._ui = None
            return
        LOGGER.info("Web console started on http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._ui is None:
            return
        close = getattr(self._ui, "close", None)
        if callable(close):
            close()

    def _profile_choices(self) -> list[str]:
        names = self.store.list_profiles()
        if not names:
            return [self._current_profile_data().name]
        return names

    def _current_profile_data(self) -> ProfileData:
        active = self.store.resolve_active_profile_name(
            self.app.deps.cfg.conversation.active_profile
        )
        if active and active in self.store.list_profiles():
            return self.store.load_profile(active)
        return self.store.build_default_profile()

    def _load_profile_for_ui(self, name: str) -> ProfileData:
        if name and name in self.store.list_profiles():
            return self.store.load_profile(name)
        return self._current_profile_data()

    def _profile_data_from_ui(
        self,
        name: str,
        persona_name: str,
        persona_name_kana: str,
        operator_name: str,
        persona_style: str,
        operator_usernames: str,
        prompt: str,
    ) -> ProfileData:
        usernames = [u.strip().lower() for u in operator_usernames.split(",") if u.strip()]
        return ProfileData(
            name=name.strip() or self._current_profile_data().name,
            persona_name=persona_name.strip(),
            persona_name_kana=persona_name_kana.strip(),
            operator_name=operator_name.strip(),
            persona_style=persona_style.strip(),
            operator_usernames=usernames,
            system_prompt_text=prompt.rstrip(),
        )

    def _apply_profile(self, name: str) -> str:
        profile = self.store.load_profile(name)
        next_cfg = self.store.apply_profile_to_config(
            self.app.deps.cfg.conversation,
            profile,
        )

        async def _do_apply() -> str:
            await self.app.reload_conversation_config(next_cfg)
            return f"Applied profile `{name}`."

        future = asyncio.run_coroutine_threadsafe(_do_apply(), self.loop)
        future.result(timeout=10)
        return f"Applied profile `{name}`."
