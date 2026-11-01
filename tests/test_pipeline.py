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
        self.env = patch.dict(os.environ, {"AZURE_OPENAI_SLEEP_S": "0", "LLM_PROVIDER": "azure",
                                           "LLM_SLEEP_S": "0"})
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
            value = self.good() if value is None else value
            if isinstance(value, dict):
                value = dict(value)
                value["scope"] = ("single" if value["is_food_review"] else "other") if type(value.get("is_food_review")) is bool else "invalid"
                if value.get("is_food_review") is True:
                    quote = value.pop("quote", None)
                    value.update(source_quote=quote, review_summary=quote)
            content = json.dumps(value)
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
               {**self.good(), "rating": "five"},
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

    def test_quote_accepts_adjacent_sentences_and_whitespace_changes(self):
        text = "选材很新鲜\n价格也实惠\n但是鱼做得一般"
        for quote in (text, "选材很新鲜 价格也实惠", "价格也实惠\n但是鱼做得一般"):
            with self.subTest(quote=quote):
                value = {**self.good(), "quote": quote}
                self.assertEqual(reviews.validate_review(value, text), value)

    def test_quote_rejects_rewriting_gaps_reordering_and_empty_text(self):
        text = "选材很新鲜\n价格也实惠\n但是鱼做得一般"
        for quote in ("所有菜都好吃", "选材很新鲜\n…\n但是鱼做得一般",
                      "但是鱼做得一般价格也实惠", "选材很新鲜但是鱼做得一般", "", "  "):
            with self.subTest(quote=quote), self.assertRaises(reviews.ReviewValidationError):
                reviews.validate_review({**self.good(), "quote": quote}, text)

    def test_quote_has_no_hard_character_limit(self):
        # Concision is a prompt preference, not a reason to reject an exact excerpt.
        text = "食材新鲜调味用心" * 24
        value = {**self.good(), "quote": text}
        self.assertGreater(len(text), 160)
        self.assertEqual(reviews.validate_review(value, text), value)

    def test_sentence_punctuation_matches_but_persists_original_subtitles(self):
        source = "开场\n选材很新鲜\n但是鱼一般\n总体值得来\n结束"
        result = reviews.validate_review({**self.good(), "quote": "选材很新鲜，但是鱼一般。总体值得来！"}, source)
        self.assertEqual(result["quote"], "选材很新鲜\n但是鱼一般\n总体值得来")
        source_with_punctuation = "选材很新鲜，但是鱼一般。"
        result = reviews.validate_review({**self.good(), "quote": "选材很新鲜但是鱼一般"}, source_with_punctuation)
        self.assertEqual(result["quote"], "选材很新鲜，但是鱼一般")

    def test_punctuation_matching_still_rejects_word_changes_and_numeric_changes(self):
        cases = [("评价不好吃而且贵", "评价好吃，而且贵。"), ("这个菜一般但是很便宜", "这个菜一般，很便宜。"),
                 ("这菜还形", "这菜还行。"), ("12.8元，挺值", "128元，挺值。"),
                 ("128元挺值", "12.8元，挺值。"), ("比例1:2", "比例12。"),
                 ("温度-5度", "温度5度。"), ("50%的人", "50的人。"), ("好\n中间省略\n便宜", "好，便宜。"),
                 ("好", "？！")]
        for source, quote in cases:
            with self.subTest(source=source, quote=quote), self.assertRaises(reviews.ReviewValidationError):
                reviews.validate_review({**self.good(), "quote": quote}, source)

    def test_scope_normalization_prevents_merging_independent_restaurants(self):
        value = {**self.good(), "scope": "multiple"}
        self.assertEqual(reviews.decode_model_review(value, "味道很好"), {
            "is_food_review": True, "restaurant_name": None, "city": "成都", "rating": None, "quote": None})
        self.assertEqual(reviews.decode_model_review({"scope": "other"}, "年度榜单"), {"is_food_review": False})
        with self.assertRaisesRegex(reviews.ReviewValidationError, "scope"):
            reviews.decode_model_review({"scope": "unknown"}, "好")

    def test_extraction_persists_source_and_corrected_summary(self):
        self.input_summary()
        response = {"scope": "single", "restaurant_name": "面馆", "city": "成都",
                    "rating": "好评", "source_quote": "味道很好", "review_summary": "味道很好。"}
        self.assertEqual(self.run_main(reviews.main, client=self.client(content=json.dumps(response))), 0)
        stored = self.read("video_reviews.json")["BV1"]
        self.assertEqual(stored, {**self.good(), "source_quote": "味道很好",
                                 "review_summary": "味道很好。", "summary_status": "complete"})

    def test_summary_can_correct_asr_while_source_remains_verbatim(self):
        source = "有菜有食物\n但是那是失误\n不是糊弄"
        value = {"scope": "single", "restaurant_name": "饭店", "city": None, "rating": "好评",
                 "source_quote": source, "review_summary": "有菜有失误，但不是糊弄。"}
        result = reviews.decode_model_review(value, source)
        self.assertEqual(result["source_quote"], source)
        self.assertEqual(result["quote"], source)
        self.assertEqual(result["review_summary"], value["review_summary"])
        reviews.validate_cached_review(result, source)

    def test_mismatched_source_does_not_block_summary_or_cache(self):
        self.input_summary()
        self.write("video_reviews.json", {"BV1": {**self.good(), "restaurant_name": "旧店名433"}})
        self.write("video_reviews_errors.json", {"BV1": {"status": "summary_pending"}})
        response = {"scope": "single", "restaurant_name": "新店名", "city": "成都", "rating": "好评",
                    "source_quote": "这个词不是原文", "review_summary": "口味出色，值得一试。"}
        client = self.client(content=json.dumps(response))
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        stored = self.read("video_reviews.json")["BV1"]
        self.assertEqual(stored["restaurant_name"], "新店名")
        self.assertEqual(stored["summary_status"], "complete")
        self.assertEqual(stored["review_summary"], response["review_summary"])
        self.assertIsNone(stored["source_quote"])
        self.assertIsNone(stored["quote"])
        self.assertIn("BV1", self.read("video_reviews_index.json"))
        self.assertEqual(self.read("video_reviews_errors.json"), {})
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_summary_accepts_missing_null_or_invalid_optional_source(self):
        base = {"scope": "single", "restaurant_name": "面馆", "city": "成都", "rating": "好评",
                "review_summary": "香味突出，不过口味偏重。"}
        for fields in ({}, {"source_quote": None}, {"source_quote": 123},
                       {"source_quote": "明显改写过的原文"}):
            with self.subTest(fields=fields):
                result = reviews.decode_model_review({**base, **fields}, "好香\n中间聊了别的\n不过有点咸")
                self.assertEqual(result["summary_status"], "complete")
                self.assertEqual(result["review_summary"], base["review_summary"])
                self.assertIsNone(result["source_quote"])
                reviews.validate_cached_review(result, "好香\n中间聊了别的\n不过有点咸")

    def test_missing_wrong_type_or_empty_summary_is_pending(self):
        base = {"scope": "single", "restaurant_name": "面馆", "city": "成都", "rating": "好评"}
        for fields in ({}, {"review_summary": []}, {"review_summary": 123},
                       {"review_summary": " "}):
            with self.subTest(fields=fields):
                result = reviews.decode_model_review({**base, "source_quote": "味道很好", **fields}, "味道很好")
                self.assertEqual(result["summary_status"], "pending")
                self.assertEqual(result["restaurant_name"], "面馆")
                self.assertEqual(result["source_quote"], "味道很好")
                with self.assertRaises(reviews.ReviewValidationError):
                    reviews.validate_cached_review(result, "味道很好")
        result = reviews.decode_model_review({**base, "review_summary": None}, "味道很好")
        self.assertEqual(result["summary_status"], "unavailable")
        reviews.validate_cached_review(result, "味道很好")

    def test_invalid_summary_persists_metadata_and_retries(self):
        self.input_summary()
        response = {"scope": "single", "restaurant_name": "新店名", "city": "成都", "rating": "好评",
                    "review_summary": []}
        client = self.client(content=json.dumps(response))
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertEqual(self.read("video_reviews.json")["BV1"]["restaurant_name"], "新店名")
        self.assertNotIn("BV1", self.read("video_reviews_index.json"))
        self.assertEqual(self.read("video_reviews_errors.json")["BV1"]["status"], "summary_pending")
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertEqual(client.chat.completions.create.call_count, 2)
        response["review_summary"] = "口味出色。"
        self.assertEqual(self.run_main(reviews.main, client=self.client(content=json.dumps(response))), 0)
        self.assertEqual(self.read("video_reviews_errors.json"), {})

    def test_prompt_requests_full_subtitle_summary_without_evidence_field(self):
        prompt = reviews.build_prompt("标题", "字幕")[-1]["content"]
        task = json.loads(prompt)["task"]
        self.assertIn('"review_summary": string or null', task)
        self.assertNotIn('"source_quote":', task)
        self.assertIn("combine evaluations", task)
        self.assertIn("Do not return source_quote", task)
        self.assertIn("Do not reverse", task)

    def test_invalid_core_metadata_still_preserves_previous_review(self):
        self.input_summary()
        self.write("video_reviews.json", {"BV1": self.good()})
        response = {"scope": "single", "restaurant_name": "新店名", "city": [], "rating": "好评",
                    "source_quote": "味道很好", "review_summary": "味道很好。"}
        self.assertEqual(self.run_main(reviews.main, client=self.client(content=json.dumps(response))), 1)
        self.assertEqual(self.read("video_reviews.json")["BV1"], self.good())

    def test_readme_shows_summary_and_pending_state_with_legacy_fallback(self):
        from generate_readme_table import build_rows, render_table
        rows = build_rows({"a": {**self.good(), "review_summary": "修正后的评价。"},
                           "b": {**self.good(), "review_summary": None, "summary_status": "pending"},
                           "c": self.good()}, {}, False)
        by_id = {r["bvid"]: r for r in rows}
        self.assertEqual(by_id["a"]["quote"], "修正后的评价。")
        self.assertEqual(by_id["b"]["quote"], "（摘述待处理）")
        self.assertEqual(by_id["c"]["quote"], "味道很好")
        self.assertIn("评价摘述", render_table(rows))

    def test_validation_failure_has_actionable_diagnostic_without_response_text(self):
        self.input_summary()
        self.assertEqual(self.run_main(reviews.main, client=self.client(content="secret-invalid-json")), 1)
        error = self.read("video_reviews_errors.json")["BV1"]
        self.assertEqual(error["validation_error"], "model response is not valid JSON")
        self.assertNotIn("secret", json.dumps(error))

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
        for constant in ("PROMPT_VERSION", "SCHEMA_VERSION", "MAX_COMPLETION_TOKENS"):
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

    def test_manual_name_wins_over_model_and_changes_invalidate_only_its_cache(self):
        self.input_summary()
        self.write("review_overrides.json", {"BV1": {"is_food_review": True, "restaurant_name": "小饭馆大厨师"}})
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(self.read("video_reviews.json")["BV1"]["restaurant_name"], "小饭馆大厨师")
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.write("review_overrides.json", {"BV1": {"is_food_review": True, "restaurant_name": "小饭馆大厨师", "note": "备注变更"}})
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.write("review_overrides.json", {"BV1": {"is_food_review": True, "restaurant_name": "人工更正店名"}})
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual(self.read("video_reviews.json")["BV1"]["restaurant_name"], "人工更正店名")

    def test_manual_exclusion_needs_no_model_or_subtitle_and_clears_old_review(self):
        self.input_summary("")
        self.write("review_overrides.json", {"BV1": {"is_food_review": False}})
        self.write("videos_summary_errors.json", {"BV1": {"status": "error"}})
        self.write("video_reviews_errors.json", {"BV1": {"status": "error"}})
        self.write("video_reviews.json", {"BV1": self.good()})
        client = self.client()
        self.assertEqual(self.run_main(reviews.main, client=client), 0)
        self.assertEqual(self.read("video_reviews.json"), {"BV1": {"is_food_review": False}})
        self.assertEqual(self.read("video_reviews_errors.json"), {})
        client.chat.completions.create.assert_not_called()

    def test_manual_name_can_override_model_nonreview_without_inventing_rating(self):
        corrected = reviews.apply_manual_override({"is_food_review": False}, {"restaurant_name": "如院"})
        self.assertTrue(corrected["is_food_review"])
        self.assertEqual(corrected["restaurant_name"], "如院")
        self.assertIsNone(corrected["rating"])
        self.assertIsNone(corrected["review_summary"])
        reviews.validate_cached_review(corrected, "subtitle")

    def test_manual_name_is_preserved_even_when_api_fails(self):
        self.input_summary()
        self.write("review_overrides.json", {"BV1": {"is_food_review": True, "restaurant_name": "如院"}})
        self.write("video_reviews.json", {"BV1": self.good()})
        client = self.client()
        client.chat.completions.create.side_effect = RuntimeError("request failed")
        self.assertEqual(self.run_main(reviews.main, client=client), 1)
        self.assertEqual(self.read("video_reviews.json")["BV1"]["restaurant_name"], "如院")
        self.assertIn("BV1", self.read("video_reviews_errors.json"))

    def test_offline_manual_corrections_are_idempotent_and_preserve_other_records(self):
        self.write("video_reviews.json", {"BV1": self.good(), "EXCLUDE": self.good(), "OTHER": self.good()})
        self.write("review_overrides.json", {"BV1": {"is_food_review": True, "restaurant_name": "如院"},
                                             "EXCLUDE": {"is_food_review": False}})
        self.write("video_reviews_index.json", {"BV1": "old", "EXCLUDE": "old", "OTHER": "keep"})
        self.write("video_reviews_errors.json", {"BV1": {"status": "summary_pending"},
                                                "EXCLUDE": {"status": "error"}})
        self.assertEqual(set(reviews.apply_saved_manual_overrides(self.data)), {"BV1", "EXCLUDE"})
        self.assertEqual(self.read("video_reviews.json")["OTHER"], self.good())
        self.assertEqual(self.read("video_reviews.json")["EXCLUDE"], {"is_food_review": False})
        self.assertEqual(self.read("video_reviews_index.json"), {"OTHER": "keep"})
        self.assertEqual(self.read("video_reviews_errors.json"), {"BV1": {"status": "summary_pending"}})
        self.assertEqual(reviews.apply_saved_manual_overrides(self.data), [])

    def test_invalid_manual_override_stops_before_model_or_output_changes(self):
        self.input_summary()
        for override in ({"restaurant_name": []}, {"is_food_review": "false"},
                         {"is_food_review": False, "restaurant_name": "矛盾记录"}):
            self.write("review_overrides.json", {"BV1": override})
            client = self.client()
            with self.subTest(override=override):
                self.assertEqual(self.run_main(reviews.main, client=client), 2)
                client.chat.completions.create.assert_not_called()
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


class ProviderTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_auto_selection_and_explicit_override(self):
        self.assertEqual(reviews.provider(), "openai")
        self.assertEqual(reviews.deployment(), "gpt-6-luna")
        os.environ.update(AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com/",
                          AZURE_OPENAI_KEY="test-azure")
        self.assertEqual(reviews.provider(), "azure")
        os.environ["OPENAI_API_KEY"] = "test-official"
        self.assertEqual(reviews.provider(), "openai")
        os.environ["LLM_PROVIDER"] = "azure"
        self.assertEqual(reviews.provider(), "azure")

    def test_official_client_uses_only_official_key_and_endpoint(self):
        os.environ.update(OPENAI_API_KEY="test-official", AZURE_OPENAI_KEY="test-azure",
                          OPENAI_BASE_URL="https://unrelated.invalid/v1")
        sdk = SimpleNamespace(OpenAI=Mock(), AzureOpenAI=Mock())
        with patch.dict(sys.modules, {"openai": sdk}):
            self.assertIs(reviews.create_client(), sdk.OpenAI.return_value)
        sdk.OpenAI.assert_called_once_with(api_key="test-official", base_url="https://api.openai.com/v1",
                                          timeout=60.0, max_retries=2)
        sdk.AzureOpenAI.assert_not_called()

    def test_azure_client_remains_compatible(self):
        os.environ.update(LLM_PROVIDER="azure", AZURE_OPENAI_KEY="test-azure",
                          AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com/",
                          AZURE_OPENAI_API_VERSION="custom-version", OPENAI_API_KEY="test-official")
        sdk = SimpleNamespace(OpenAI=Mock(), AzureOpenAI=Mock())
        with patch.dict(sys.modules, {"openai": sdk}):
            reviews.create_client()
        sdk.AzureOpenAI.assert_called_once_with(api_version="custom-version",
            azure_endpoint="https://example.openai.azure.com/", api_key="test-azure")
        sdk.OpenAI.assert_not_called()

    def test_missing_official_key_never_falls_back_to_azure(self):
        os.environ.update(LLM_PROVIDER="openai", AZURE_OPENAI_KEY="test-azure")
        with self.assertRaisesRegex(reviews.ConfigurationError, "OPENAI_API_KEY"):
            reviews.create_client()

    def test_bad_provider_fails_before_writing_or_calling(self):
        os.environ["LLM_PROVIDER"] = "typo"
        client = Mock()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(reviews.main(["--data-dir", tmp], client=client), 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertIn("LLM_PROVIDER", output.getvalue())
        client.chat.completions.create.assert_not_called()

    def test_official_json_request_and_model_override(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"scope": "other"}'), finish_reason="stop")])
        self.assertEqual(reviews.extract_review(client, "title", "text"), {"is_food_review": False})
        options = client.chat.completions.create.call_args.kwargs
        self.assertEqual(options["model"], "gpt-6-luna")
        self.assertEqual(options["reasoning_effort"], "none")
        self.assertEqual(options["response_format"], {"type": "json_object"})
        self.assertEqual(options["max_completion_tokens"], 1600)
        os.environ["OPENAI_MODEL"] = "another-model"
        reviews.extract_review(client, "title", "text")
        options = client.chat.completions.create.call_args.kwargs
        self.assertEqual(options["model"], "another-model")
        self.assertNotIn("reasoning_effort", options)
        os.environ["LLM_PROVIDER"] = "azure"
        reviews.extract_review(client, "title", "text")
        options = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("reasoning_effort", options)
        self.assertNotIn("response_format", options)

    def test_cache_separates_providers_and_models_but_not_keys(self):
        item = {"title": "t", "subtitle_text": "text"}
        os.environ["OPENAI_API_KEY"] = "first-key"
        signature = reviews.compute_signature(item)
        os.environ.update(OPENAI_API_KEY="rotated-key", AZURE_OPENAI_DEPLOYMENT="irrelevant")
        self.assertEqual(signature, reviews.compute_signature(item))
        os.environ["OPENAI_MODEL"] = "different-model"
        self.assertNotEqual(signature, reviews.compute_signature(item))
        os.environ.update(LLM_PROVIDER="azure", AZURE_OPENAI_DEPLOYMENT="gpt-6-luna",
                          AZURE_OPENAI_ENDPOINT=reviews.OPENAI_BASE_URL)
        self.assertNotEqual(signature, reviews.compute_signature(item))


if __name__ == "__main__":
    unittest.main()
