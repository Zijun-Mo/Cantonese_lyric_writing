import unittest
from types import SimpleNamespace
from unittest import mock

from src.input.schema import LyricInput
from src.pipeline import run_pipeline


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


if __name__ == "__main__":
    unittest.main()
