#!/usr/bin/env python3
import json
import os
import time
import random
import hashlib
import urllib.parse
from pathlib import Path

import requests

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

BASE = "https://api.bilibili.com"


def sleep_slow(min_s=1.2, max_s=2.2):
    time.sleep(random.uniform(min_s, max_s))


def get_wbi_keys(session: requests.Session):
    url = f"{BASE}/x/web-interface/nav"
    resp = session.get(
        url,
        headers={
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        },
    )
    resp.raise_for_status()
    data = resp.json()["data"]["wbi_img"]
    img_key = data["img_url"].rsplit("/", 1)[1].split(".", 1)[0]
    sub_key = data["sub_url"].rsplit("/", 1)[1].split(".", 1)[0]
    return img_key, sub_key


def gen_mixin_key(img_key: str, sub_key: str):
    raw = img_key + sub_key
    mixed = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)
    return mixed[:32]


def wbi_sign(params: dict, img_key: str, sub_key: str):
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    mixin_key = gen_mixin_key(img_key, sub_key)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


def fetch_all_archives(vmid: int, session: requests.Session, img_key: str, sub_key: str):
    pn = 1
    ps = 50
    all_videos = []
    while True:
        params = wbi_sign(
            {"mid": vmid, "ps": ps, "pn": pn, "order": "pubdate"},
            img_key,
            sub_key,
        )
        url = f"{BASE}/x/space/wbi/arc/search"
        r = session.get(url, params=params)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") == -352:
            print(json.dumps(payload, ensure_ascii=True))
            return []
        if payload.get("code") != 0 or not payload.get("data") or "list" not in payload["data"]:
            print(json.dumps(payload, ensure_ascii=True))
            break
        data = payload["data"]
        vlist = data["list"]["vlist"]
        if not vlist:
            break
        all_videos.extend(vlist)
        total = data["page"]["count"]
        if pn * ps >= total:
            break
        pn += 1
        sleep_slow()
    return all_videos


def fetch_all_archives_app(vmid: int, session: requests.Session):
    all_videos = []
    last_aid = 0
    ps = 30
    while True:
        params = {"vmid": vmid, "ps": ps}
        if last_aid:
            params["aid"] = last_aid
        url = "https://app.biliapi.com/x/v2/space/archive/cursor"
        r = session.get(url, params=params)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != 0 or not payload.get("data"):
            print(json.dumps(payload, ensure_ascii=True))
            break
        data = payload["data"]
        items = data.get("item") or []
        if not items:
            break
        for it in items:
            all_videos.append(
                {
                    "bvid": it.get("bvid"),
                    "title": it.get("title"),
                    "aid": int(it.get("param", 0)) if it.get("param") else 0,
                }
            )
        last_aid = int(items[-1].get("param", 0)) if items[-1].get("param") else 0
        if not data.get("has_next"):
            break
        sleep_slow()
    return all_videos


def fetch_video_detail(bvid: str, session: requests.Session):
    url = f"{BASE}/x/web-interface/view"
    r = session.get(url, params={"bvid": bvid})
    r.raise_for_status()
    return r.json()["data"]


def fetch_player_subtitles(bvid: str, cid: int, session: requests.Session, img_key: str, sub_key: str):
    url = f"{BASE}/x/player/wbi/v2"
    params = wbi_sign({"bvid": bvid, "cid": cid}, img_key, sub_key)
    r = session.get(url, params=params)
    r.raise_for_status()
    data = r.json()["data"]
    subtitle = data.get("subtitle") or {}
    return subtitle.get("subtitles") or []


def download_subtitle(sub_url: str, out_path: Path, session: requests.Session):
    if not sub_url:
        return
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    r = session.get(sub_url)
    r.raise_for_status()
    out_path.write_bytes(r.content)


def load_index(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(text: str):
    return "".join(c for c in text if c.isascii() and (c.isalnum() or c in " _-")).strip()


def is_ai_subtitle(item: dict) -> bool:
    lan = item.get("lan") or ""
    if lan.startswith("ai-"):
        return True
    return bool(item.get("ai_type"))


def pick_subtitles(subtitles, preferred_langs, allow_ai_langs):
    if not subtitles:
        return []

    def is_preferred(item):
        lan = item.get("lan") or ""
        if lan in preferred_langs or lan in allow_ai_langs:
            return True
        lan_doc = item.get("lan_doc") or ""
        return any(tag in lan_doc for tag in ("中文", "简体", "繁体"))

    non_ai_preferred = [s for s in subtitles if is_preferred(s) and not is_ai_subtitle(s)]
    if non_ai_preferred:
        return non_ai_preferred

    ai_preferred = [
        s
        for s in subtitles
        if is_preferred(s) and is_ai_subtitle(s) and (s.get("lan") in allow_ai_langs)
    ]
    if ai_preferred:
        return ai_preferred

    non_ai = [s for s in subtitles if not is_ai_subtitle(s)]
    if non_ai:
        return non_ai

    if allow_ai_langs:
        return [s for s in subtitles if s.get("lan") in allow_ai_langs]
    return []


def main():
    VMID = 3546888255048212  # TODO: 替换成目标 up 主 mid
    SESSDATA = os.environ.get("SESSDATA", "")  # 可选：需要登录字幕时填 cookie 中的 SESSDATA
    COOKIE = os.environ.get("COOKIE", "")  # 可选：从浏览器复制的完整 Cookie 字符串
    PREFERRED_LANGS = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW"]
    ALLOW_AI_LANGS = ["ai-zh"]

    out_dir = Path("data")
    out_dir.mkdir(parents=True, exist_ok=True)
    subs_dir = out_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    index_path = out_dir / "videos.json"
    index = load_index(index_path)

    session = requests.Session()
    session.headers.update(
        {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
    )
    if COOKIE:
        for part in COOKIE.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                session.cookies.set(k.strip(), v.strip(), domain=".bilibili.com")
    if SESSDATA:
        session.cookies.set("SESSDATA", SESSDATA, domain=".bilibili.com")

    img_key, sub_key = get_wbi_keys(session)
    sleep_slow()

    videos = fetch_all_archives(VMID, session, img_key, sub_key)
    if not videos:
        print("wbi arc search blocked or empty; try adding cookies or SESSDATA.")
        print("falling back to app cursor...")
        videos = fetch_all_archives_app(VMID, session)
    existing = set(index.keys())
    work = []
    for v in videos:
        bvid = v.get("bvid")
        if not bvid:
            continue
        if bvid not in existing:
            work.append(("new", v))
        elif not index.get(bvid, {}).get("subs_done"):
            work.append(("resume", v))

    new_count = sum(1 for t, _ in work if t == "new")
    resume_count = sum(1 for t, _ in work if t == "resume")
    print(f"total videos: {len(videos)}, new: {new_count}, resume: {resume_count}")

    for kind, v in work:
        bvid = v["bvid"]
        record = index.get(bvid)
        if kind == "new" or not record or not record.get("pages"):
            detail = fetch_video_detail(bvid, session)
            sleep_slow()
            record = {
                "bvid": bvid,
                "aid": detail.get("aid"),
                "title": detail.get("title"),
                "pubdate": detail.get("pubdate"),
                "desc": detail.get("desc"),
                "stat": detail.get("stat"),
                "pages": detail.get("pages"),
                "subs_done": False,
            }
            index[bvid] = record
            save_index(index_path, index)

        title = record.get("title") or v.get("title") or bvid
        pages = record.get("pages") or []
        sub_records = record.get("subtitle_records") or []
        seen = {(r.get("cid"), r.get("lan"), r.get("subtitle_url")) for r in sub_records}
        for p in pages:
            cid = p["cid"]
            subtitles = fetch_player_subtitles(bvid, cid, session, img_key, sub_key)
            sleep_slow()
            subtitles = pick_subtitles(subtitles, PREFERRED_LANGS, ALLOW_AI_LANGS)
            if not subtitles:
                continue
            for s in subtitles:
                lan = s.get("lan", "unknown")
                sub_url = s["subtitle_url"]
                if not sub_url:
                    continue
                base = safe_name(title) or bvid
                fname = f"{base}_{bvid}_{cid}_{lan}.json"
                out_path = subs_dir / fname
                if out_path.exists():
                    rel_path = str(Path("data") / "subs" / fname)
                else:
                    download_subtitle(sub_url, out_path, session)
                    sleep_slow()
                    msg = f"saved subtitle: {out_path}"
                    print(msg.encode("ascii", "backslashreplace").decode("ascii"))
                    rel_path = str(Path("data") / "subs" / fname)
                key = (cid, lan, sub_url)
                if key not in seen:
                    sub_records.append(
                        {
                            "cid": cid,
                            "lan": lan,
                            "subtitle_url": sub_url,
                            "file": rel_path,
                        }
                    )
                    seen.add(key)

        record["subtitle_records"] = sub_records
        record["subs_done"] = True
        index[bvid] = record
        save_index(index_path, index)


if __name__ == "__main__":
    main()
