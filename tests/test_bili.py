import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bili
import summarize_videos as summaries

BV = "BV1RirSBKED9"
BV2 = "BV1bK411W797"
SRT = "1\n00:00:01,250 --> 00:00:03,500\n好吃\n\n2\n00:00:04,000 --> 00:00:05,000\n下次再来\n"


def entry(cid=123, subtitles=True):
    tracks = {"danmaku": [{"ext": "xml", "url": f"https://comment.bilibili.com/{cid}.xml"}]}
    if subtitles:
        tracks.update({"ai-zh": [{"ext": "srt", "data": SRT.replace("好吃", "AI")}],
                       "zh-Hans": [{"ext": "srt", "data": SRT}]})
    return {"id": BV, "title": "餐厅", "duration": 15, "timestamp": 100,
            "view_count": 25, "subtitles": tracks}


class FakeYDL:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extract_info(self, url, download=False):
        self.calls.append((url, download))
        if isinstance(self.result, Exception):
            raise self.result
        return copy.deepcopy(self.result)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.index = {}

    def acquire(self, info):
        ydl = FakeYDL(info)
        status = bili.acquire(BV, ydl, self.index, self.root, self.data)
        return status, ydl

    def test_multipart_chinese_preference_srt_conversion_and_rerun(self):
        status, _ = self.acquire({"title": "总标题", "entries": [entry(123), entry(456)]})
        self.assertEqual(status, "complete")
        record = self.index[BV]
        self.assertEqual(record["title"], "总标题")
        self.assertEqual([p["cid"] for p in record["pages"]], [123, 456])
        self.assertEqual(len(record["subtitle_records"]), 2)
        sub = record["subtitle_records"][0]
        self.assertEqual(sub["file"], f"subs/{BV}/123/zh-Hans.json")
        self.assertEqual(sub["lan"], "zh-Hans")
        payload = json.loads((self.data / sub["file"]).read_text())
        self.assertEqual(payload["body"][0], {"from": 1.25, "to": 3.5, "content": "好吃"})
        status, ydl = self.acquire(RuntimeError("must not call extractor"))
        self.assertEqual(status, "skipped")
        self.assertEqual(ydl.calls, [])

    def test_legacy_windows_path_reused(self):
        path = self.data / "subs/old.json"
        bili.save_index(path, {"body": [{"content": "以前的字幕"}]})
        self.index[BV] = {"pages": [{"cid": 123}], "subs_done": True,
                         "subtitle_records": [{"cid": 123, "lan": "ai-zh", "file": "data\\subs\\old.json"}]}
        status, ydl = self.acquire(RuntimeError("no network"))
        self.assertEqual(status, "skipped")
        self.assertFalse(ydl.calls)

    def test_empty_legacy_done_retried(self):
        self.index[BV] = {"pages": [{"cid": 123}], "subs_done": True, "subtitle_records": []}
        status, ydl = self.acquire(entry())
        self.assertEqual(status, "complete")
        self.assertEqual(len(ydl.calls), 1)

    def test_repaired_language_change_allows_summary_and_cached_rerun(self):
        for damaged in (None, "{bad"):
            with self.subTest(damaged=damaged):
                path = self.data / "subs/old.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                if damaged is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(damaged)
                self.index = {BV: {"pages": [{"cid": 123}], "subtitle_records": [
                    {"cid": 123, "lan": "ai-zh", "file": "subs/old.json"}]}}
                self.assertEqual(self.acquire(entry())[0], "complete")
                bili.save_index(self.data / "videos.json", self.index)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(summaries.main(["--data-dir", str(self.data)]), 0)
                summary = bili.load_index(self.data / "videos_summary.json")[BV]
                self.assertEqual(summary["subtitle_text"], "好吃\n下次再来")
                status, ydl = self.acquire(RuntimeError("must use cache"))
                self.assertEqual(status, "skipped")
                self.assertFalse(ydl.calls)
                if damaged is not None:
                    self.assertEqual(path.read_text(), damaged)

    def test_cached_valid_chinese_removes_only_invalid_references(self):
        self.acquire(entry())
        valid = copy.deepcopy(self.index[BV]["subtitle_records"])
        translation = {"cid": 123, "lan": "en", "file": "subs/en.json"}
        bili.save_index(self.data / translation["file"], {"body": [{"content": "good"}]})
        self.index[BV]["subtitle_records"] += [translation,
            {"cid": "123", "lan": "ai-zh", "file": "subs/missing.json"}]
        status, ydl = self.acquire(RuntimeError("must use cache"))
        self.assertEqual(status, "skipped")
        self.assertFalse(ydl.calls)
        self.assertEqual(self.index[BV]["subtitle_records"], valid + [translation])
        summaries.read_tracks(self.index[BV], self.data)

    def test_failed_repair_preserves_invalid_reference_for_retry(self):
        tracks = [{"cid": 123, "lan": "ai-zh", "file": "subs/missing.json"}]
        self.index[BV] = {"pages": [{"cid": 123}], "subtitle_records": tracks}
        self.assertEqual(self.acquire(entry(subtitles=False))[0], "incomplete")
        self.assertEqual(self.index[BV]["subtitle_records"], tracks)

    def test_empty_or_missing_tracks_not_complete(self):
        for info, expected in [(entry(subtitles=False), "subtitle_unavailable"),
                               (entry(), "subtitle_empty_or_invalid")]:
            if expected.endswith("invalid"):
                info["subtitles"]["zh-Hans"][0]["data"] = ""
            with self.subTest(expected=expected):
                self.index = {}
                status, _ = self.acquire(info)
                self.assertEqual(status, "incomplete")
                self.assertFalse(self.index[BV]["subs_done"])
                self.assertEqual(self.index[BV]["subtitle_status"]["123"], expected)
                self.assertEqual(self.index[BV]["subtitle_records"], [])

    def test_denied_preserves_old_successful_data(self):
        self.acquire(entry())
        self.index[BV]["pages"].append({"cid": 456, "duration": 20})
        previous = copy.deepcopy(self.index[BV])
        status, _ = self.acquire(PermissionError("403 signed_url secret"))
        self.assertEqual(status, "access_denied")
        self.assertEqual(self.index[BV]["subtitle_records"], previous["subtitle_records"])
        self.assertEqual(self.index[BV]["pages"], previous["pages"])
        self.assertEqual(self.index[BV]["title"], previous["title"])
        self.assertFalse(self.index[BV]["subs_done"])
        self.assertNotIn("secret", json.dumps(self.index))

    def test_partial_part_preserves_success_and_retries_missing(self):
        status, _ = self.acquire({"entries": [entry(123), entry(456, subtitles=False)]})
        self.assertEqual(status, "incomplete")
        path = self.data / self.index[BV]["subtitle_records"][0]["file"]
        before = path.stat().st_mtime_ns
        self.acquire({"entries": [entry(123), entry(456)]})
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertTrue(self.index[BV]["subs_done"])

    def test_unsupported_english_only_and_missing_cid(self):
        info = entry(subtitles=False)
        info["subtitles"]["en"] = [{"ext": "srt", "data": SRT}]
        self.assertEqual(self.acquire(info)[0], "incomplete")
        self.index = {}
        info["subtitles"].pop("danmaku")
        self.assertEqual(self.acquire(info)[0], "extraction_failed")

    def test_login_warning_has_explicit_status(self):
        logger = bili.ExtractionLog()
        class LoginYDL(FakeYDL):
            params = {"logger": logger}

            def extract_info(self, *args, **kwargs):
                logger.warning("Subtitles are only available when logged in")
                return entry(subtitles=False)
        status = bili.acquire(BV, LoginYDL(None), self.index, self.root, self.data)
        self.assertEqual(status, "login_required")
        self.assertEqual(self.index[BV]["subtitle_status"]["123"], "login_required")
        self.assertFalse(self.index[BV]["subs_done"])

    def test_malformed_cached_subtitle_is_refetched(self):
        self.acquire(entry())
        path = self.data / self.index[BV]["subtitle_records"][0]["file"]
        for bad_row in ["bad", {"content": 123}, {"content": "text", "from": -1},
                        {"content": "text", "to": float("inf")},
                        {"content": "text", "from": float("nan")},
                        {"content": "text", "from": 5, "to": 2},
                        {"content": "text", "from": "0"}, {"content": "text", "to": True}]:
            with self.subTest(row=bad_row):
                bili.save_index(path, {"body": [{"content": "valid"}, bad_row]})
                status, ydl = self.acquire(entry())
                self.assertEqual(status, "complete")
                self.assertEqual(len(ydl.calls), 1)
                self.assertTrue(bili.valid_body(bili.load_index(path)))

    def test_missing_cookie_file_stops_before_extractor(self):
        factory = unittest.mock.Mock()
        with redirect_stdout(io.StringIO()):
            result = bili.main(["--root", str(self.root), "--bvid", BV,
                                "--cookie-file", str(self.root / "missing-cookie.txt")], factory)
        self.assertEqual(result, 1)
        factory.assert_not_called()
        self.assertFalse((self.root / "missing-cookie.txt").exists())

    def test_bili_denial_codes(self):
        for code in (-352, -401):
            with self.subTest(code=code):
                self.index = {}
                self.assertEqual(self.acquire(RuntimeError(f"API code {code}"))[0], "access_denied")

    def test_atomic_failure_preserves_previous(self):
        path = self.data / "videos.json"
        bili.save_index(path, {"old": True})
        with patch("bili.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                bili.save_index(path, {"new": True})
        self.assertEqual(bili.load_index(path), {"old": True})
        self.assertEqual(list(self.data.iterdir()), [path])

    def test_cli_url_file_limit_and_custom_data_dir(self):
        inputs = self.root / "urls.txt"
        inputs.write_text(f"# input\nhttps://www.bilibili.com/video/{BV}\n{BV2}\n", encoding="utf-8")
        options = []
        def factory(opts):
            options.append(opts)
            return FakeYDL(entry())
        with redirect_stdout(io.StringIO()):
            result = bili.main(["--url-file", str(inputs), "--root", str(self.root),
                                "--data-dir", "out", "--limit", "1"], factory)
        self.assertEqual(result, 0)
        index = bili.load_index(self.root / "out/videos.json")
        self.assertEqual(list(index), [BV])
        self.assertTrue(options[0]["skip_download"])
        self.assertNotIn("cookiefile", options[0])

    def test_access_denied_stops_remaining_work(self):
        ydl = FakeYDL(PermissionError("HTTP Error 403"))
        output = io.StringIO()
        with redirect_stdout(output):
            code = bili.main(["--root", str(self.root), "--bvid", BV, "--bvid", BV2],
                             lambda opts: ydl)
        self.assertEqual(code, 1)
        self.assertEqual(len(ydl.calls), 1)
        self.assertIn('"unprocessed": 1', output.getvalue())

    def test_cli_failure_exit_summary_and_partial_discovery(self):
        def broken_entries():
            yield {"id": BV}
            raise RuntimeError("pagination failed")
        class Discovery(FakeYDL):
            def extract_info(self, *args, **kwargs):
                return {"entries": broken_entries()}
        def factory(opts):
            return Discovery(None) if opts.get("extract_flat") else FakeYDL(entry())
        output = io.StringIO()
        with redirect_stdout(output):
            code = bili.main(["--root", str(self.root)], factory)
        self.assertEqual(code, 1)
        self.assertIn('"discovery": "failed_or_partial"', output.getvalue())
        self.assertTrue(bili.load_index(self.data / "videos.json")[BV]["subs_done"])
        with redirect_stdout(io.StringIO()):
            code = bili.main(["--root", str(self.root), "--bvid", BV2],
                             lambda opts: FakeYDL(PermissionError("denied")))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
