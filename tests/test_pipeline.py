"""Offline regression tests. No API credentials or network requests are used."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import summarize_videos as summaries
import analyze_reviews as reviews


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.args = ["--data-dir", str(self.data)]
        self.env = patch.dict(os.environ, {"AZURE_OPENAI_SLEEP_S": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, name, value):
        path = self.data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def read(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def run_main(self, fn, args=None, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(self.args + (args or []), **kw)

    def source(self):
        self.write("subs/zh.json", {"body": [{"from": 1.2, "to": 3.4, "content": "味道很好"}]})
        record = {"title": "探店", "desc": "成都面馆", "pages": [{"duration": 50}],
                  "subtitle_records": [{"cid": 7, "lan": "ai-zh", "file": "data\\subs\\zh.json"}]}
        self.write("videos.json", {"BV1": record})
        return record

    def input_summary(self, text="味道很好", bvid="BV1"):
        self.write("videos_summary.json", {bvid: {"title": "探店", "desc": "成都面馆", "subtitle_text": text}})

    def client(self, value=None, content=None, finish="stop"):
        if content is None:
            content = json.dumps(value if value is not None else self.good())
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])
        return client

    def good(self):
        return {"is_food_review": True, "restaurant_name": "面馆", "city": "成都",
                "rating": "好评", "quote": "味道很好"}

    def test_legacy_paths_timestamps_desc_and_cwd(self):
        self.source()
        old = Path.cwd()
        try:
            os.chdir(self.data)
            self.assertEqual(self.run_main(summaries.main), 0)
        finally:
            os.chdir(old)
        item = self.read("videos_summary.json")["BV1"]
        self.assertEqual(item["subtitle_text"], "味道很好")
        self.assertEqual(item["desc"], "成都面馆")
        self.assertEqual(item["subtitle_segments"][0], {
            "content": "味道很好", "from": 1.2, "to": 3.4, "cid": 7, "lan": "ai-zh"})
        self.assertEqual(summaries.INPUT_PATH, ROOT / "data/videos.json")

    def test_content_hash_changes_with_same_mtime(self):
        record = self.source()
        path = self.data / "subs/zh.json"
        stamp = path.stat().st_mtime_ns
        sig = summaries.compute_signature(record, self.data)
        self.write("subs/zh.json", {"body": [{"content": "味道不好"}]})
        os.utime(path, ns=(stamp, stamp))
        self.assertNotEqual(sig, summaries.compute_signature(record, self.data))

    def test_one_preferred_track_per_cid(self):
        record = self.source()
        self.write("subs/en.json", {"body": [{"content": "good"}]})
        self.write("subs/manual.json", {"body": [{"content": "人工中文"}]})
        self.write("subs/part2.json", {"body": [{"content": "第二部分"}]})
        record["subtitle_records"] += [
            {"cid": 7, "lan": "ai-en", "file": "subs/en.json"},
            {"cid": 7, "lan": "zh-CN", "file": "subs/manual.json"},
            {"cid": 8, "lan": "ai-zh", "file": "subs/part2.json"}]
        self.write("videos.json", {"BV1": record})
        self.run_main(summaries.main)
        self.assertEqual(self.read("videos_summary.json")["BV1"]["subtitle_text"], "人工中文\n第二部分")

    def test_missing_corrupt_and_empty_subtitle_preserve_good_and_retry(self):
        self.source()
        self.run_main(summaries.main)
        before = self.read("videos_summary.json")
        path = self.data / "subs/zh.json"
        for bad in (None, "{bad", '{"body": []}', '{"body": [{"content": 42}]}'):
            with self.subTest(bad=bad):
                if bad is None:
                    path.unlink()
                else:
                    path.write_text(bad)
                self.assertEqual(self.run_main(summaries.main), 1)
                self.assertEqual(self.read("videos_summary.json"), before)
                self.assertNotIn("BV1", self.read("videos_summary_index.json"))
                self.assertIn("BV1", self.read("videos_summary_errors.json"))
        self.source()
        self.assertEqual(self.run_main(summaries.main), 0)
        self.assertEqual(self.read("videos_summary_errors.json"), {})

    def test_missing_multipart_page_preserves_summary_and_blocks_analysis(self):
        record = self.source()
        record["pages"] = [{"cid": "7", "duration": 50}]
        self.write("videos.json", {"BV1": record})
        self.assertEqual(self.run_main(summaries.main), 0)
        before = self.read("videos_summary.json")
        record["pages"].append({"cid": 8, "duration": 30})
        self.write("videos.json", {"BV1": record})
        self.assertEqual(self.run_main(summaries.main), 1)
        self.assertEqual(self.read("videos_summary.json"), before)
        self.assertIn("BV1", self.read("videos_summary_errors.json"))
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        client.chat.completions.create.assert_not_called()

    def test_track_selection_normalizes_numeric_cids(self):
        tracks = [{"cid": 7, "lan": "ai-zh", "file": "a"},
                  {"cid": "7", "lan": "zh-CN", "file": "b"}]
        self.assertEqual(summaries.preferred_tracks(tracks), [tracks[1]])

    def test_summary_without_records_does_not_create_empty_output(self):
        self.write("videos.json", {"BV1": {"title": "test"}})
        self.assertEqual(self.run_main(summaries.main), 1)
        self.assertFalse((self.data / "videos_summary.json").exists())

    def test_analysis_cache_force_selection_and_limit(self):
        self.input_summary()
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.run_main(reviews.main, client=client)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.run_main(reviews.main, ["--force", "--limit", "0"], client=client)
        self.run_main(reviews.main, ["--force", "--bvid", "OTHER"], client=client)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.run_main(reviews.main, ["--force", "--bvid", "BV1", "--limit", "1"], client=client)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_empty_input_is_retryable_not_false(self):
        self.input_summary("")
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertFalse((self.data / "video_reviews.json").exists())
        self.assertEqual(self.read("video_reviews_index.json"), {})
        client.chat.completions.create.assert_not_called()
        self.input_summary()
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(self.read("video_reviews_errors.json"), {})

    def test_api_failure_preserves_good_and_retries(self):
        self.input_summary()
        self.run_main(reviews.main, client=self.client())
        before = self.read("video_reviews.json")
        client = self.client()
        client.chat.completions.create.side_effect = RuntimeError("secret request URL")
        self.assertEqual(self.run_main(reviews.main, ["--force"], client=client), 1)
        self.assertEqual(self.read("video_reviews.json"), before)
        self.assertEqual(self.read("video_reviews_index.json"), {})
        self.assertNotIn("secret", (self.data / "video_reviews_errors.json").read_text())
        self.assertEqual(self.run_main(reviews.main, client=self.client()), 0)

    def test_bad_json_types_rating_quote_or_truncation_are_failures(self):
        bad = [[], {"is_food_review": "false"}, {**self.good(), "city": []},
               {**self.good(), "rating": "five"}, {**self.good(), "quote": "invented"},
               {"is_food_review": False, "error": "old cached error"}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                reviews.extract_review(self.client(value), "title", "味道很好")
        for client in (self.client(content="{oops"), self.client(content=""), self.client(finish="length")):
            with self.assertRaises(ValueError):
                reviews.extract_review(client, "title", "味道很好")

    def test_legitimate_negative_and_uncertain_rating(self):
        self.assertEqual(reviews.extract_review(self.client({"is_food_review": False}), "title", "味道很好"),
                         {"is_food_review": False})
        value = {**self.good(), "rating": None, "quote": None}
        self.assertEqual(reviews.validate_review(value, "味道很好"), value)

    def test_upstream_failure_blocks_stale_summary(self):
        self.input_summary()
        self.write("videos_summary_errors.json", {"BV1": {"status": "error"}})
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        client.chat.completions.create.assert_not_called()

    def test_new_upstream_failure_is_reported_without_summary(self):
        self.write("videos.json", {"NEW": {"title": "new video"}})
        self.assertEqual(self.run_main(summaries.main), 1)
        self.assertFalse((self.data / "videos_summary.json").exists())
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertIn("NEW", self.read("video_reviews_errors.json"))
        client.chat.completions.create.assert_not_called()
        self.assertFalse((self.data / "video_reviews.json").exists())

    def test_analysis_still_requires_input_when_no_upstream_failures_exist(self):
        client = self.client()
        with self.assertRaises(FileNotFoundError):
            self.run_main(reviews.main, client=client)
        client.chat.completions.create.assert_not_called()

    def test_missing_summary_error_respects_selection_limit_and_recovery(self):
        self.input_summary()
        self.write("videos_summary_errors.json", {"NEW": {"status": "error"}})
        self.write("video_reviews.json", {"NEW": self.good()})
        self.write("video_reviews_index.json", {"NEW": "stale"})
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, ["--limit", "0"], client=client), 0)
        self.assertEqual(self.run_main(reviews.main, ["--bvid", "BV1"], client=client), 0)
        self.assertEqual(self.run_main(reviews.main, ["--bvid", "NEW"], client=client), 1)
        self.assertEqual(self.read("video_reviews.json")["NEW"], self.good())
        self.assertNotIn("NEW", self.read("video_reviews_index.json"))
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.write("videos_summary_errors.json", {})
        self.write("videos_summary.json", {
            "NEW": {"title": "recovered", "subtitle_text": "味道很好"}})
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertNotIn("NEW", self.read("video_reviews_errors.json"))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_cache_signature_covers_model_prompt_schema_description(self):
        item = {"title": "a", "subtitle_text": "b", "desc": "c"}
        original = reviews.compute_signature(item)
        with patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT": "new-model"}):
            self.assertNotEqual(original, reviews.compute_signature(item))
        for constant in ("PROMPT_VERSION", "SCHEMA_VERSION"):
            with patch.object(reviews, constant, 999):
                self.assertNotEqual(original, reviews.compute_signature(item))
        self.assertNotEqual(original, reviews.compute_signature({**item, "desc": "d"}))

    def test_per_record_checkpoint_survives_interruption(self):
        self.write("videos_summary.json", {key: {"title": "t", "subtitle_text": "味道很好"}
                                           for key in ("BV1", "BV2")})
        client = self.client()
        response = client.chat.completions.create.return_value
        client.chat.completions.create.side_effect = [response, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_main(reviews.main, client=client)
        self.assertEqual(list(self.read("video_reviews.json")), ["BV1"])
        self.assertEqual(list(self.read("video_reviews_index.json")), ["BV1"])

    def test_unknown_bvid_fails_without_changes(self):
        self.source()
        self.input_summary()
        for fn in (summaries.main, reviews.main):
            self.assertEqual(self.run_main(fn, ["--bvid", "MISSING"]), 2)
        self.assertFalse((self.data / "video_reviews.json").exists())

    def test_missing_config_stops_once_with_actionable_message(self):
        self.input_summary()
        with patch.dict(os.environ, {"AZURE_OPENAI_KEY": "", "AZURE_OPENAI_ENDPOINT": ""}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(reviews.main(self.args), 2)
        self.assertIn("AZURE_OPENAI_KEY", output.getvalue())
        self.assertFalse((self.data / "video_reviews_errors.json").exists())

    def test_atomic_write_failure_preserves_previous_file(self):
        path = self.write("checkpoint.json", {"old": True})
        with patch.object(summaries.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                summaries.save_json(path, {"new": True})
        self.assertEqual(self.read("checkpoint.json"), {"old": True})
        self.assertEqual(list(self.data.glob("*.tmp")), [])

    def test_help_and_import_do_not_need_credentials_or_openai(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AZURE_OPENAI")}
        for script in ("summarize_videos.py", "analyze_reviews.py"):
            result = subprocess.run([sys.executable, str(ROOT / script), "--help"],
                                    cwd=self.data, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        code = "import sys; sys.modules['openai'] = None; import analyze_reviews"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
