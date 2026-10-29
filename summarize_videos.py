#!/usr/bin/env python3
"""Build summaries without silently discarding unavailable subtitle evidence."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
INPUT_PATH = DATA_DIR / "videos.json"
OUTPUT_PATH = DATA_DIR / "videos_summary.json"
INDEX_PATH = DATA_DIR / "videos_summary_index.json"
SUMMARY_VERSION = 2


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    """Replace one checkpoint atomically; interrupted writes leave the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            name = f.name
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def get_stat_value(stat: dict, key: str) -> int:
    try:
        return int(stat.get(key, 0))
    except (TypeError, ValueError):
        return 0


def total_duration(pages) -> int:
    return sum(get_stat_value(p, "duration") for p in pages or [])


def subtitle_path(value: str, data_dir=DATA_DIR) -> Path:
    """Accept old Windows paths and portable data-relative paths, regardless of cwd."""
    path = Path(value.replace("\\", "/"))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return Path(data_dir).joinpath(*path.parts[1:])
    return Path(data_dir) / path


def subtitle_segments_from_file(path: Path, raw=None) -> list:
    payload = json.loads(path.read_bytes() if raw is None else raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        raise ValueError("subtitle body must be a list")
    segments = []
    for item in payload["body"]:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError("invalid subtitle content")
        for key in ("from", "to"):
            value = item.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("invalid subtitle timestamp")
        if item.get("from") is not None and item.get("to") is not None and item["to"] < item["from"]:
            raise ValueError("subtitle ends before it starts")
        if item["content"].strip():
            segments.append({k: item[k] for k in ("from", "to", "content") if k in item})
    if not segments:
        raise ValueError("empty subtitle body")
    return segments


def subtitle_text_from_file(path: Path) -> str:
    return "\n".join(s["content"] for s in subtitle_segments_from_file(path))


def preferred_tracks(records: list) -> list:
    """One track per CID: human Chinese, AI Chinese, then other languages."""
    def preference(track):
        lang = track.get("lan", "")
        chinese = lang.startswith("zh") or lang in ("ai-zh", "ai-zh-CN")
        return (not chinese, lang.startswith("ai-"), lang, track.get("file", ""))
    groups = {}
    for track in records:
        groups.setdefault(str(track.get("cid")), []).append(track)
    return [min(group, key=preference) for group in groups.values()]


def read_tracks(record: dict, data_dir=DATA_DIR) -> list:
    records = record.get("subtitle_records") or []
    if not records:
        raise ValueError("no subtitle records; fetch subtitles before summarizing")
    known_cids = {str(page["cid"]) for page in record.get("pages", [])
                  if page.get("cid") is not None}
    track_cids = {str(track.get("cid")) for track in records}
    missing_cids = known_cids - track_cids
    if missing_cids:
        raise ValueError("missing subtitle tracks for page CID(s): " + ", ".join(sorted(missing_cids)))
    loaded = []
    # Validate every reference, even an unselected translation, rather than hide damage.
    for track in records:
        if not track.get("file"):
            raise ValueError("subtitle record has no file")
        path = subtitle_path(track["file"], data_dir)
        raw = path.read_bytes()
        segments = subtitle_segments_from_file(path, raw)
        loaded.append({**track, "segments": segments,
                       "sha256": hashlib.sha256(raw).hexdigest()})
    return loaded


def compute_signature(record: dict, data_dir=DATA_DIR, tracks=None) -> str:
    tracks = read_tracks(record, data_dir) if tracks is None else tracks
    payload = {"version": SUMMARY_VERSION, "record": record,
               "subtitles": [{"cid": t.get("cid"), "lan": t.get("lan"),
                              "sha256": t["sha256"]} for t in tracks]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def make_summary(bvid, record, tracks):
    segments = []
    for track in preferred_tracks(tracks):
        segments.extend({**s, "cid": track.get("cid"), "lan": track.get("lan")}
                        for s in track["segments"])
    stat = record.get("stat") or {}
    return {
        "bvid": bvid, "title": record.get("title"), "desc": record.get("desc", ""),
        "pubdate": record.get("pubdate"),
        **{k: get_stat_value(stat, k) for k in ("view", "favorite", "coin", "share", "like")},
        "duration_total": total_duration(record.get("pages")),
        "subtitle_text": "\n".join(s["content"] for s in segments),
        "subtitle_segments": segments,
    }


def parse_args(argv=None, description=None):
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--bvid", action="append", help="Process only this BVID (repeatable)")
    parser.add_argument("--limit", type=int, help="Maximum uncached records to attempt")
    parser.add_argument("--force", action="store_true", help="Ignore successful cache entries")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    args.data_dir = (ROOT / args.data_dir).resolve()
    return args


def main(argv=None):
    args = parse_args(argv)
    data = args.data_dir
    # Required inputs must not turn a typo/missing file into an apparently successful run.
    videos = json.loads((data / "videos.json").read_text(encoding="utf-8"))
    if args.bvid and set(args.bvid) - videos.keys():
        print("Unknown BVID(s): " + ", ".join(sorted(set(args.bvid) - videos.keys())))
        return 2
    summary = load_json(data / "videos_summary.json", {})
    index = load_json(data / "videos_summary_index.json", {})
    errors = load_json(data / "videos_summary_errors.json", {})
    updated = attempted = failed = 0
    for bvid, record in videos.items():
        if args.bvid and bvid not in args.bvid:
            continue
        if args.limit is not None and attempted >= args.limit:
            break
        try:
            tracks = read_tracks(record, data)
            sig = compute_signature(record, data, tracks)
            if not args.force and index.get(bvid) == sig and bvid in summary and bvid not in errors:
                continue
            item = make_summary(bvid, record, tracks)
            attempted += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            attempted += 1
            failed += 1
            errors[bvid] = {"status": "error", "error": type(exc).__name__, "message": str(exc)}
            index.pop(bvid, None)
            save_json(data / "videos_summary_index.json", index)
            save_json(data / "videos_summary_errors.json", errors)
            print(f"{bvid}: subtitle unavailable or invalid; previous summary preserved")
            continue
        summary[bvid] = item
        index[bvid] = sig
        errors.pop(bvid, None)
        # Write data first: a crash before the index update only causes a safe retry.
        save_json(data / "videos_summary.json", summary)
        save_json(data / "videos_summary_index.json", index)
        save_json(data / "videos_summary_errors.json", errors)
        updated += 1
    print(f"updated: {updated}, failed: {failed}, total: {len(summary)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
