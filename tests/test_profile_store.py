import tempfile
import unittest

from reachy_twitch_voice.config import ConversationConfig
from reachy_twitch_voice.profile_store import ProfileData, ProfileStore


class ProfileStoreTest(unittest.TestCase):
    def test_save_load_and_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConversationConfig(
                persona_name="NUVA",
                persona_name_kana="ヌーバ",
                operator_name="op",
                persona_style="style",
                operator_usernames=["alice"],
                system_prompt_text="default prompt",
                profile_storage_dir=tmp,
            )
            store = ProfileStore(tmp, cfg)
            saved = store.save_profile(
                ProfileData(
                    name="My Profile",
                    persona_name="P1",
                    persona_name_kana="ピーワン",
                    operator_name="boss",
                    persona_style="friendly",
                    operator_usernames=["alice", "bob"],
                    system_prompt_text="hello prompt",
                )
            )
            self.assertEqual(saved, "My_Profile")
            self.assertIn(saved, store.list_profiles())

            loaded = store.load_profile(saved)
            self.assertEqual(loaded.persona_name, "P1")
            self.assertEqual(loaded.operator_usernames, ["alice", "bob"])
            self.assertEqual(loaded.system_prompt_text, "hello prompt\n")

            store.set_active_profile(saved)
            self.assertEqual(store.get_active_profile(), saved)

    def test_build_default_profile_uses_cfg_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConversationConfig(
                persona_name="DefaultName",
                persona_name_kana="デフォルト",
                operator_name="operator",
                persona_style="calm",
                operator_usernames=["tom"],
                system_prompt_text="built-in prompt",
                profile_storage_dir=tmp,
            )
            store = ProfileStore(tmp, cfg)
            profile = store.build_default_profile()
            self.assertEqual(profile.persona_name, "DefaultName")
            self.assertEqual(profile.system_prompt_text, "built-in prompt")


if __name__ == "__main__":
    unittest.main()
