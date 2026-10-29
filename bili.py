#!/usr/bin/env python3
"""Acquire Bilibili metadata/subtitles through yt-dlp; never download video media."""
import argparse
import copy
import json
import math
import os
import re
import tempfile
from pathlib import Path

DEFAULT_MID = "3546888255048212"
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


class ExtractionLog:
    """Retain warning categories, not URLs or cookie-bearing diagnostics."""
    def __init__(self):
        self.login_required = False

    def debug(self, message):
        pass

    def warning(self, message):
        text = message.lower()
        if "log in" in text or "logged in" in text or "login" in text:
            self.login_required = True

    def error(self, message):
        self.warning(message)


def load_index(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def save_index(path, data):
    """Replace a complete JSON file, never expose a partially written index/subtitle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def subtitle_path(value, root, data_dir):
    """New paths are data-relative; accept the old root-relative Windows paths."""
    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return data_dir.joinpath(*path.parts[1:])
    return data_dir / path


def valid_body(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        return False
    nonempty = False
    for row in payload["body"]:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            return False
        for key in ("from", "to"):
            value = row.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                return False
        if row.get("from") is not None and row.get("to") is not None and row["to"] < row["from"]:
            return False
        nonempty = nonempty or bool(row["content"].strip())
    return nonempty


def valid_subtitle(record, root, data_dir):
    try:
        return bool(record.get("file")) and valid_body(
            load_index(subtitle_path(record["file"], root, data_dir)))
    except (OSError, ValueError, TypeError):
        return False


def chinese(lang):
    return lang.lower().removeprefix("ai-").split("-")[0] in ("zh", "cmn")


def complete(record, root, data_dir):
    pages = record.get("pages") or []
    good = {str(s.get("cid")) for s in record.get("subtitle_records", [])
            if chinese(s.get("lan", "")) and valid_subtitle(s, root, data_dir)}
    return bool(pages) and all(str(p.get("cid")) in good for p in pages)


def pick_subtitle(info):
    tracks = []
    for source in ("subtitles", "automatic_captions"):
        for lang, formats in (info.get(source) or {}).items():
            if not chinese(lang):
                continue
            ai = source == "automatic_captions" or lang.startswith("ai-")
            rank = {"zh-Hans": 0, "zh-CN": 1, "zh": 2, "zh-Hant": 3, "zh-TW": 4}.get(lang, 5)
            for track in formats:
                if track.get("ext") in ("json", "srt"):
                    tracks.append(((ai, rank, lang, track.get("ext") != "json"), lang, track))
    return min(tracks, key=lambda item: item[0])[1:] if tracks else None


def subtitle_payload(track, ydl):
    data = track.get("data")
    if data is None:
        url = track.get("url")
        if not url:
            raise ValueError("empty subtitle")
        # yt-dlp owns authentication and networking; no handwritten Bili API client.
        from yt_dlp.networking import Request
        with ydl.urlopen(Request(url, headers=track.get("http_headers") or {})) as response:
            data = response.read().decode("utf-8-sig")
    if track["ext"] == "json":
        payload = json.loads(data) if isinstance(data, str) else data
    else:
        def seconds(value):
            hours, minutes, seconds = value.replace(",", ".").split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        body = []
        for block in re.split(r"\n\s*\n", data.replace("\r\n", "\n").strip()):
            lines = block.splitlines()
            for number, line in enumerate(lines):
                match = re.fullmatch(r"(\d+:\d{2}:\d{2}[,.]\d+)\s+-->\s+(\d+:\d{2}:\d{2}[,.]\d+).*", line)
                if match:
                    body.append({"from": seconds(match[1]), "to": seconds(match[2]),
                                 "content": "\n".join(lines[number + 1:]).strip()})
                    break
        payload = {"body": body}
    if not valid_body(payload):
        raise ValueError("empty or invalid subtitle")
    return payload


def part_cid(info):
    if info.get("cid"):
        return int(info["cid"])
    # Public yt-dlp Bilibili output carries CID in its danmaku URL.
    for track in (info.get("subtitles") or {}).get("danmaku", []):
        match = re.search(r"/(\d+)\.xml(?:\?|$)", track.get("url", ""))
        if match:
            return int(match[1])
    raise ValueError("yt-dlp returned no CID")


def parts(info):
    if not info:
        raise ValueError("empty extractor result")
    if "entries" in info:
        for entry in info["entries"]:
            yield from parts(entry)
    else:
        yield info


def acquire(bvid, ydl, index, root, data_dir):
    old = index.get(bvid, {})
    if complete(old, root, data_dir):
        old.update(subs_done=True, acquisition_status="complete")
        return "skipped"
    record = copy.deepcopy(old)
    record.setdefault("bvid", bvid)
    record.setdefault("subtitle_records", [])
    record["subs_done"] = False
    logger = getattr(ydl, "params", {}).get("logger")
    if isinstance(logger, ExtractionLog):
        logger.login_required = False
    try:
        info = ydl.extract_info(f"https://www.bilibili.com/video/{bvid}", download=False)
        entries = list(parts(info))
        if not entries:
            raise ValueError("no parts returned")
        # Validate the complete part list before replacing existing metadata.
        cids = [part_cid(entry) for entry in entries]
        first = entries[0]
        for target, source in (("title", "title"), ("desc", "description"), ("pubdate", "timestamp")):
            value = info.get(source, first.get(source))
            if value is not None:
                record[target] = value
        record.setdefault("aid", None)
        stat = record.setdefault("stat", {})
        for target, source in (("view", "view_count"), ("like", "like_count"), ("reply", "comment_count")):
            if first.get(source) is not None:
                stat[target] = first[source]
        old_pages = {str(p["cid"]): p for p in old.get("pages", [])}
        pages = []
        for number, (entry, cid) in enumerate(zip(entries, cids), 1):
            page = dict(old_pages.get(str(cid), {}))
            page.update(cid=cid, page=number, part=entry.get("title", ""), duration=entry.get("duration", 0))
            pages.append(page)
        record["pages"] = pages
        statuses = {}
        for entry, cid in zip(entries, cids):
            if any(str(s.get("cid")) == str(cid) and chinese(s.get("lan", ""))
                   and valid_subtitle(s, root, data_dir) for s in record["subtitle_records"]):
                statuses[str(cid)] = "complete"
                continue
            chosen = pick_subtitle(entry)
            if not chosen:
                statuses[str(cid)] = "login_required" if getattr(logger, "login_required", False) else "subtitle_unavailable"
                continue
            lang, track = chosen
            try:
                payload = subtitle_payload(track, ydl)
                relative = Path("subs") / bvid / str(cid) / (re.sub(r"[^A-Za-z0-9_-]", "_", lang) + ".json")
                save_index(data_dir / relative, payload)
                record["subtitle_records"] = [s for s in record["subtitle_records"]
                    if not (str(s.get("cid")) == str(cid) and s.get("lan") == lang)]
                record["subtitle_records"].append({"cid": cid, "lan": lang,
                    "subtitle_url": "", "file": relative.as_posix()})
                statuses[str(cid)] = "complete"
            except ValueError:
                statuses[str(cid)] = "subtitle_empty_or_invalid"
            except Exception:
                statuses[str(cid)] = "subtitle_failed"
        record["subtitle_status"] = statuses
        record["subs_done"] = complete(record, root, data_dir)
        record["acquisition_status"] = ("complete" if record["subs_done"] else
                                         "login_required" if "login_required" in statuses.values() else "incomplete")
    except Exception as error:
        # Keep old successful files and metadata. Do not persist signed URLs/errors.
        message = str(error).lower()
        record["acquisition_status"] = (
            "login_required" if getattr(logger, "login_required", False) else
            "access_denied" if any(token in message for token in ("403", "412", "429", "-352", "-401", "rate limit", "forbidden"))
            else "extraction_failed")
        record["error_type"] = type(error).__name__
    index[bvid] = record
    return record["acquisition_status"]


def video_id(value):
    match = BV_RE.search(value)
    if not match:
        raise ValueError("Expected a BV ID or Bilibili video URL")
    return match[0]


def main(argv=None, ydl_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bvid", action="append", default=[])
    parser.add_argument("--url-file", type=Path, help="One Bilibili video URL or BV ID per line")
    parser.add_argument("--mid", default=DEFAULT_MID, help="Uploader for discovery when no explicit inputs")
    parser.add_argument("--limit", type=int, help="Maximum number of videos, not parts")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--cookie-file", type=Path, help="Optional local Netscape cookie file; never committed")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    root = args.root.resolve()
    data_dir = args.data_dir if args.data_dir.is_absolute() else root / args.data_dir
    try:
        index = load_index(data_dir / "videos.json")
        values = list(args.bvid)
        if args.url_file:
            values.extend(line.strip() for line in args.url_file.read_text(encoding="utf-8-sig").splitlines()
                          if line.strip() and not line.lstrip().startswith("#"))
        ids = list(dict.fromkeys(video_id(value) for value in values))
        if args.url_file and not ids:
            raise ValueError("URL file has no video IDs")
        if ydl_factory is None:
            from yt_dlp import YoutubeDL
            ydl_factory = YoutubeDL
        options = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                   "ignoreerrors": False, "quiet": True, "no_warnings": False,
                   "ignore_no_formats_error": True, "cachedir": False, "logger": ExtractionLog(),
                   "socket_timeout": 20, "retries": 1, "extractor_retries": 1,
                   "sleep_interval_requests": 1.2}
        if args.cookie_file:
            cookie_path = args.cookie_file.expanduser().resolve()
            if not cookie_path.is_file():
                raise FileNotFoundError("cookie file does not exist")
            options["cookiefile"] = str(cookie_path)
        discovery = "explicit"
        discovery_failed = False
        if not ids:
            discovery = "limited" if args.limit else "complete"
            try:
                with ydl_factory({**options, "extract_flat": True, "playlistend": args.limit}) as ydl:
                    result = ydl.extract_info(f"https://space.bilibili.com/{args.mid}/video", download=False)
                    for entry in result["entries"]:
                        if not entry:
                            raise ValueError("missing discovery entry")
                        bvid = video_id(entry.get("id") or entry.get("url", ""))
                        if bvid not in ids:
                            ids.append(bvid)
                        if args.limit and len(ids) >= args.limit:
                            break
                    if not ids:
                        raise ValueError("empty discovery result")
            except Exception:
                discovery, discovery_failed = "failed_or_partial", True
        ids = ids[:args.limit] if args.limit else ids
        counts = {}
        with ydl_factory(options) as ydl:
            for bvid in ids:
                status = acquire(bvid, ydl, index, root, data_dir)
                counts[status] = counts.get(status, 0) + 1
                save_index(data_dir / "videos.json", index)
                print(f"{bvid}: {status}")
                if status in ("login_required", "access_denied"):
                    break
        print(json.dumps({"discovery": discovery, "selected": len(ids),
                          "unprocessed": len(ids) - sum(counts.values()), "results": counts}))
        return int(discovery_failed or any(status not in ("complete", "skipped") for status in counts))
    except (ImportError, OSError, ValueError, TypeError) as error:
        print(f"Acquisition stopped ({type(error).__name__}); check dependencies, inputs and local paths.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
