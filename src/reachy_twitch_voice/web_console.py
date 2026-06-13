from __future__ import annotations

import asyncio
import logging
import os
import queue as _queue_mod
import tempfile as _tmpfile_wc
from typing import Any

from .orchestrator import AppOrchestrator
from .profile_store import ProfileData, ProfileStore

LOGGER = logging.getLogger(__name__)

_WEB_AUDIO_DIR = os.path.join(_tmpfile_wc.gettempdir(), "reachy_web_audio")


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
        self._web_audio_queue: _queue_mod.Queue[str] = _queue_mod.Queue()
        self._web_audio_served: list[str] = []

    def _enqueue_web_audio(self, wav_path: str) -> None:
        self._web_audio_queue.put(wav_path)

    def start(self) -> None:
        try:
            import gradio as gr
        except Exception as exc:
            LOGGER.warning("Web console disabled: gradio import failed: %s", exc)
            return

        self.app.register_web_audio_sink(self._enqueue_web_audio)

        self._gradio = gr
        current = self._current_profile_data()
        profiles = self._profile_choices()
        initial_mode = "manual_text" if self.app.input_mode == "manual" else "twitch"
        manual_enabled = initial_mode == "manual_text"

        def _format_status(s: str) -> str:
            icons = {
                "connected": "🟢 接続中",
                "auth_failed": "🔴 認証失敗",
                "connecting": "🟡 接続中...",
                "reconnecting": "🟡 再接続中...",
            }
            return f"**Twitch IRC**: {icons.get(s, f'🟡 {s}')}"

        with gr.Blocks(title="Reachy Mini Twitch Voice Console") as ui:
            gr.Markdown(
                "# 🤖 Reachy Mini Twitch Voice Console\n"
                "LAN公開・無認証です。同一LAN内でのみ利用してください。"
            )

            connection_status_md = gr.Markdown(_format_status(self.app.twitch_connection_status))
            status_timer = gr.Timer(value=2.0)

            with gr.Tabs():
                with gr.Tab("🔌 接続"):
                    gr.Markdown(
                        f"**Twitch Nick**: `{self.app.deps.cfg.twitch.nick}`  |  "
                        f"**Channel**: `#{self.app.deps.cfg.twitch.channel}`"
                    )
                    gr.Markdown(
                        "> ⚠️ トークンは `TWITCH_NICK` と同じアカウントで発行してください。"
                        "nick が一致しないと認証失敗になります。"
                    )
                    token_input = gr.Textbox(
                        label="Twitch OAuth Token",
                        type="password",
                        placeholder="oauth:xxxxxxxxxxxxxxxxxxxxxx",
                        info="更新ボタンで即時再接続します。値はブラウザに返しません。",
                    )
                    token_status = gr.Markdown("")
                    with gr.Row():
                        token_btn = gr.Button("🔑 トークン更新", variant="primary")
                        reconnect_btn = gr.Button("🔄 今すぐ再接続")

                with gr.Tab("⌨️ 入力"):
                    mode_select = gr.Radio(
                        label="Input Source Mode",
                        choices=["twitch", "manual_text"],
                        value=initial_mode,
                    )
                    input_status = gr.Markdown(f"現在の入力モード: `{initial_mode}`")
                    manual_user_name = gr.Textbox(
                        label="Manual User Name", value="manual_tester", interactive=manual_enabled
                    )
                    manual_text = gr.TextArea(
                        label="Manual Input Text", value="", lines=4, interactive=manual_enabled
                    )
                    send_btn = gr.Button("Send Manual Input", interactive=manual_enabled)

                with gr.Tab("🔊 音声"):
                    audio_target_select = gr.Radio(
                        label="音声出力先",
                        choices=["robot", "web"],
                        value=getattr(self.app, "audio_output_target", "robot"),
                    )
                    audio_target_status = gr.Markdown("")
                    tts_voice_select = gr.Dropdown(
                        label="TTS Voice (OpenAI)",
                        choices=[
                            "alloy", "ash", "ballad", "coral", "echo",
                            "fable", "onyx", "nova", "sage", "shimmer",
                        ],
                        value=self.app.deps.cfg.reachy.tts_openai_voice,
                    )
                    tts_voice_status = gr.Markdown("")
                    web_audio_note = gr.Markdown(
                        "💡 **Web出力時**: OBS Browser Source で :7860 をキャプチャすると音声が乗ります。",
                        visible=False,
                    )
                    web_audio_player = gr.Audio(
                        label="TTS音声（Webモード）", autoplay=True, visible=False
                    )
                    audio_timer = gr.Timer(value=0.5)

                with gr.Tab("🎉 イベント"):
                    channel_events_cb = gr.Checkbox(
                        label="チャンネルイベントに反応する",
                        value=self.app.channel_events_enabled,
                    )
                    channel_event_types_cg = gr.CheckboxGroup(
                        label="反応するイベント種別",
                        choices=["raid", "sub", "resub", "subgift", "submysterygift"],
                        value=list(self.app.channel_event_types),
                    )
                    channel_events_status = gr.Markdown("")

                with gr.Tab("🧑 ペルソナ"):
                    profile_name = gr.Textbox(label="Profile Name", value=current.name)
                    profile_select = gr.Dropdown(
                        label="Saved Profiles",
                        choices=profiles,
                        value=current.name if current.name in profiles else None,
                        allow_custom_value=True,
                    )
                    persona_name = gr.Textbox(label="Persona Name", value=current.persona_name)
                    persona_name_kana = gr.Textbox(
                        label="Persona Name Kana", value=current.persona_name_kana
                    )
                    operator_name = gr.Textbox(label="Operator Name", value=current.operator_name)
                    persona_style = gr.Textbox(label="Persona Style", value=current.persona_style)
                    operator_usernames = gr.Textbox(
                        label="Operator Usernames",
                        value=",".join(current.operator_usernames),
                    )
                    prompt_text = gr.TextArea(
                        label="System Prompt", value=current.system_prompt_text, lines=14
                    )
                    preview = gr.TextArea(
                        label="Profile Preview",
                        value=current.system_prompt_text,
                        lines=10,
                        interactive=False,
                    )
                    status = gr.Markdown("")
                    with gr.Row():
                        save_btn = gr.Button("Save")
                        apply_btn = gr.Button("Apply")
                        reload_btn = gr.Button("Load")

            # --- callbacks ---

            def _mode_status_text(mode: str, detail: str | None = None) -> str:
                base = f"現在の入力モード: `{mode}`"
                if not detail:
                    return base
                return f"{base}\n\n{detail}"

            def _mode_ui_state(mode: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
                manual = mode == "manual_text"
                return (
                    gr.update(interactive=manual),
                    gr.update(interactive=manual),
                    gr.update(interactive=manual),
                    _mode_status_text(mode),
                )

            def _switch_mode(mode: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
                async def _do_switch() -> None:
                    await self.app.set_input_mode(mode)

                future = asyncio.run_coroutine_threadsafe(_do_switch(), self.loop)
                future.result(timeout=10)
                return _mode_ui_state(mode)

            def _toggle_channel_events(enabled: bool, types: list[str]) -> str:
                async def _do_toggle() -> None:
                    await self.app.set_channel_events_enabled(enabled)
                    await self.app.set_channel_event_types(types)

                future = asyncio.run_coroutine_threadsafe(_do_toggle(), self.loop)
                future.result(timeout=10)
                state = "ON" if enabled else "OFF"
                return f"チャンネルイベント: {state} / 種別: {', '.join(types) if types else 'なし'}"

            def _send_manual_input(user_name: str, text: str, mode: str) -> tuple[str, str]:
                if mode != "manual_text":
                    return _mode_status_text(mode, "手入力は `manual_text` モード時のみ送信できます。"), text
                if not text.strip():
                    return _mode_status_text(mode, "空のテキストは送信できません。"), text

                async def _do_send() -> str:
                    await self.app.consume_manual_text(
                        text=text.strip(), user_name=user_name.strip() or "manual_tester"
                    )
                    return f"Sent manual input as `{user_name.strip() or 'manual_tester'}`."

                future = asyncio.run_coroutine_threadsafe(_do_send(), self.loop)
                result = future.result(timeout=30)
                return _mode_status_text(mode, result), ""

            def _switch_audio_target(target: str) -> tuple[dict[str, Any], dict[str, Any], str]:
                async def _do() -> None:
                    await self.app.set_audio_output_target(target)

                future = asyncio.run_coroutine_threadsafe(_do(), self.loop)
                future.result(timeout=10)
                is_web = target == "web"
                return (
                    gr.update(visible=is_web),
                    gr.update(visible=is_web),
                    f"音声出力先: `{target}`",
                )

            def _poll_web_audio() -> dict[str, Any]:
                try:
                    path = self._web_audio_queue.get_nowait()
                except Exception:
                    return gr.update()
                self._web_audio_served.append(path)
                if len(self._web_audio_served) > 5:
                    old = self._web_audio_served.pop(0)
                    try:
                        if os.path.exists(old):
                            os.unlink(old)
                    except OSError:
                        pass
                return gr.update(value=path)

            def _update_token(token: str) -> tuple[str, str]:
                if not token.strip():
                    return "", "⚠️ トークンが空です。"

                async def _do() -> None:
                    await self.app.set_twitch_token(token)

                future = asyncio.run_coroutine_threadsafe(_do(), self.loop)
                future.result(timeout=15)
                return "", "✅ トークンを更新しました。再接続を試みています..."

            def _reconnect() -> str:
                self.app.request_reconnect()
                return "🔄 再接続を要求しました。"

            def _poll_connection_status() -> str:
                return _format_status(self.app.twitch_connection_status)

            def _switch_voice(voice: str) -> str:
                async def _do() -> None:
                    await self.app.set_tts_voice(voice)

                future = asyncio.run_coroutine_threadsafe(_do(), self.loop)
                future.result(timeout=10)
                return f"TTS Voice: `{voice}`"

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
                    name, p_name, p_kana, op_name, p_style, op_users, prompt
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
                    selected_name, p_name, p_kana, op_name, p_style, op_users, prompt
                )
                saved = self.store.save_profile(data)
                self.store.set_active_profile(saved)
                applied = self._apply_profile(saved)
                return (
                    applied,
                    gr.update(choices=self._profile_choices(), value=saved),
                    prompt,
                )

            # --- wire events ---
            token_btn.click(
                fn=_update_token, inputs=[token_input], outputs=[token_input, token_status]
            )
            reconnect_btn.click(fn=_reconnect, outputs=[token_status])
            status_timer.tick(fn=_poll_connection_status, outputs=[connection_status_md])

            mode_select.change(
                fn=_switch_mode,
                inputs=[mode_select],
                outputs=[manual_user_name, manual_text, send_btn, input_status],
            )
            send_btn.click(
                fn=_send_manual_input,
                inputs=[manual_user_name, manual_text, mode_select],
                outputs=[input_status, manual_text],
            )

            audio_target_select.change(
                _switch_audio_target,
                inputs=[audio_target_select],
                outputs=[web_audio_note, web_audio_player, audio_target_status],
            )
            tts_voice_select.change(
                fn=_switch_voice, inputs=[tts_voice_select], outputs=[tts_voice_status]
            )
            audio_timer.tick(_poll_web_audio, outputs=[web_audio_player])

            channel_events_cb.change(
                fn=_toggle_channel_events,
                inputs=[channel_events_cb, channel_event_types_cg],
                outputs=[channel_events_status],
            )
            channel_event_types_cg.change(
                fn=_toggle_channel_events,
                inputs=[channel_events_cb, channel_event_types_cg],
                outputs=[channel_events_status],
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
                allowed_paths=[_WEB_AUDIO_DIR],
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
