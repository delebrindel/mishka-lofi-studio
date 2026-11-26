import unittest
from unittest.mock import patch

from steps import music


class GenerationPayloadTest(unittest.TestCase):
    def test_saved_language_enables_vocals_without_changing_legacy_defaults(self):
        song = {"caption": "Expressive voice over a guitar riff", "lyrics": "[Verse]\nLa lluvia canta"}
        with patch.object(music, "settings", return_value=music.DEFAULTS), patch.object(music, "unwrap", return_value={"task_id": "test"}), patch.object(music.requests, "post") as post:
            music.submit(song, None)
            legacy = post.call_args.kwargs["json"]
            self.assertEqual(legacy["vocal_language"], "unknown")
            self.assertIn("vocals", legacy["lm_negative_prompt"])
            self.assertTrue(music.lint(song))

            vocal = {**song, "vocal_language": "es"}
            music.submit(vocal, None)
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["vocal_language"], "es")
            self.assertEqual(payload["lyrics"], song["lyrics"])
            self.assertEqual(payload["lm_negative_prompt"], "")
            self.assertFalse(payload["use_cot_language"])
            self.assertEqual(music.lint(vocal), [])

            with self.assertRaises(ValueError):
                music.submit({**song, "vocal_language": "Spanish"}, None)
            self.assertEqual(post.call_count, 2)
