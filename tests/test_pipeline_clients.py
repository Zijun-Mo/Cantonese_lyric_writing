import unittest
from types import SimpleNamespace
from unittest import mock

from src.input.schema import LyricInput
from src.pipeline import _llm_segment_sentences, run_pipeline
from src.preprocess.jianpu_parser import parse_jianpu
from src.preprocess.mandarin_segmenter import segment_all_bars


class _DummyClient:
    api_key = "test-key"
    model = "base"

    def mode_label(self):
        return "base"

    def for_quality_retry(self):
        return _QualityClient()


class _QualityClient:
    api_key = "test-key"
    model = "quality"

    def mode_label(self):
        return "quality"


class _PunctClient:
    def __init__(self):
        self.user_messages = []

    def chat(self, messages, temperature=0.1, max_tokens=100):
        user = messages[-1]["content"]
        self.user_messages.append(user)
        return "曾许下心愿，等待你的出现。"


class PipelineClientTest(unittest.TestCase):
    def test_polish_uses_quality_retry_client(self):
        score = SimpleNamespace(bars=[], total_slots=0)
        lyric_input = LyricInput(jianpu="", mandarin_seed="", theme_tags=[], style_tags=[])

        with mock.patch("src.pipeline.parse_jianpu", return_value=score), \
                mock.patch("src.pipeline.segment_all_bars", return_value=[]), \
                mock.patch("src.pipeline.score_to_0243_templates", return_value=[]), \
                mock.patch("src.pipeline._llm_segment_sentences", return_value={}), \
                mock.patch("src.pipeline.iterative_polish", return_value=0) as polish, \
                self.assertLogs("src.pipeline", level="INFO"):
            run_pipeline(lyric_input, client=_DummyClient(), enable_polish=True)

        self.assertIsInstance(polish.call_args.args[0], _QualityClient)

    def test_english_seed_token_is_copied_not_generated(self):
        lyric_input = LyricInput(
            jianpu="1 2 3",
            mandarin_seed="oh 出发",
            theme_tags=[],
            style_tags=[],
        )
        fill_calls = []

        def fake_fill_single_bar(client, slot_count, seed_text, target_0243, *args, **kwargs):
            fill_calls.append((slot_count, seed_text, list(target_0243)))
            return ["出發"]

        high_score = {
            "total": 0.95,
            "tone": 0.95,
            "semantic": 0.95,
            "naturalness": 0.95,
            "phrasing": 1.0,
            "rhyme_style": 0.6,
            "char_count": 2,
            "target_count": 2,
        }

        with mock.patch("src.pipeline._llm_segment_sentences", return_value={}), \
                mock.patch("src.pipeline._fill_single_bar", side_effect=fake_fill_single_bar), \
                mock.patch("src.pipeline.score_candidate", return_value=high_score), \
                mock.patch("src.pipeline.text_to_0243_list", return_value=[("出", 3), ("發", 3)]), \
                self.assertLogs("src.pipeline", level="INFO"):
            result = run_pipeline(lyric_input, client=_DummyClient(), enable_polish=False)

        self.assertEqual(result["full_lyric"], "oh出發")
        self.assertEqual(fill_calls, [(2, "出发", [2, 4])])
        self.assertEqual(result["bars"][0]["slot_count"], 3)
        self.assertEqual(result["bars"][0]["fill_slot_count"], 2)
        self.assertEqual(result["bars"][0]["lyric_placeholders"], [{"slot_index": 0, "text": "oh"}])

    def test_llm_sentence_punctuate_ignores_seed_spaces(self):
        score = parse_jianpu("1 2 3 4 5 6 | 1 2 3 4")
        semantics = segment_all_bars("曾许下心愿 等待 | 你的出现")
        client = _PunctClient()

        with self.assertLogs("src.pipeline", level="INFO") as logs:
            sentence_map = _llm_segment_sentences(score, semantics, client)

        self.assertIn("曾许下心愿等待你的出现", client.user_messages[0])
        self.assertNotIn("标点校验失败", "\n".join(logs.output))
        self.assertEqual(sentence_map[0]["sentence_bars"], [0, 1])


if __name__ == "__main__":
    unittest.main()
