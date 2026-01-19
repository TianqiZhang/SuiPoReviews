#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path


INPUT_PATH = Path("data/videos.json")
OUTPUT_PATH = Path("data/videos_summary.json")
INDEX_PATH = Path("data/videos_summary_index.json")


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_stat_value(stat: dict, key: str) -> int:
    try:
        return int(stat.get(key, 0))
    except (TypeError, ValueError):
        return 0


def total_duration(pages) -> int:
    total = 0
    for p in pages or []:
        try:
            total += int(p.get("duration", 0))
        except (TypeError, ValueError):
            continue
    return total


def subtitle_text_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    body = payload.get("body") or []
    lines = []
    for item in body:
        content = item.get("content")
        if content:
            lines.append(str(content))
    return "\n".join(lines)


def compute_signature(record: dict) -> str:
    stat = record.get("stat") or {}
    pages = record.get("pages") or []
    subtitle_records = record.get("subtitle_records") or []
    subtitle_files = []
    subtitle_mtimes = []
    for s in subtitle_records:
        rel = s.get("file")
        if not rel:
            continue
        path = Path(rel)
        subtitle_files.append(rel)
        try:
            subtitle_mtimes.append((rel, int(os.path.getmtime(path))))
        except OSError:
            subtitle_mtimes.append((rel, 0))
    sig_obj = {
        "title": record.get("title"),
        "pubdate": record.get("pubdate"),
        "stat": {
            "view": get_stat_value(stat, "view"),
            "favorite": get_stat_value(stat, "favorite"),
            "coin": get_stat_value(stat, "coin"),
            "share": get_stat_value(stat, "share"),
            "like": get_stat_value(stat, "like"),
        },
        "duration_total": total_duration(pages),
        "subtitle_files": subtitle_files,
        "subtitle_mtimes": subtitle_mtimes,
    }
    payload = json.dumps(sig_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def main():
    videos = load_json(INPUT_PATH, {})
    summary = load_json(OUTPUT_PATH, {})
    index = load_json(INDEX_PATH, {})

    updated = 0
    for bvid, record in videos.items():
        sig = compute_signature(record)
        if index.get(bvid) == sig and bvid in summary:
            continue

        stat = record.get("stat") or {}
        pages = record.get("pages") or []
        subtitle_records = record.get("subtitle_records") or []

        subtitle_chunks = []
        for s in subtitle_records:
            rel = s.get("file")
            if not rel:
                continue
            subtitle_chunks.append(subtitle_text_from_file(Path(rel)))
        subtitle_text = "\n".join([c for c in subtitle_chunks if c])

        summary[bvid] = {
            "bvid": bvid,
            "title": record.get("title"),
            "pubdate": record.get("pubdate"),
            "view": get_stat_value(stat, "view"),
            "favorite": get_stat_value(stat, "favorite"),
            "coin": get_stat_value(stat, "coin"),
            "share": get_stat_value(stat, "share"),
            "like": get_stat_value(stat, "like"),
            "duration_total": total_duration(pages),
            "subtitle_text": subtitle_text,
        }
        index[bvid] = sig
        updated += 1

    save_json(OUTPUT_PATH, summary)
    save_json(INDEX_PATH, index)
    print(f"updated: {updated}, total: {len(summary)}")


if __name__ == "__main__":
    main()
