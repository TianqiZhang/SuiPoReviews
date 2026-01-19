#!/usr/bin/env python3
import hashlib
import json
import os
import time
from pathlib import Path

from openai import AzureOpenAI


INPUT_PATH = Path("data/videos_summary.json")
OUTPUT_PATH = Path("data/video_reviews.json")
INDEX_PATH = Path("data/video_reviews_index.json")

API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.2-chat")

print(f"Using Azure OpenAI endpoint: {AZURE_ENDPOINT}, deployment: {AZURE_DEPLOYMENT}")

REQUEST_SLEEP_S = float(os.getenv("AZURE_OPENAI_SLEEP_S", "0.5"))


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_signature(item: dict) -> str:
    payload = {
        "title": item.get("title", ""),
        "subtitle_text": item.get("subtitle_text", ""),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def build_prompt(title: str, subtitle_text: str) -> list:
    system = (
        "You are an information extraction assistant. "
        "Return ONLY a compact JSON object, no markdown, no extra text."
    )
    user = {
        "title": title,
        "subtitle_text": subtitle_text,
        "task": (
            "Decide if this is a restaurant visit/review video (探店/餐馆评测). "
            "If NO, return {\"is_food_review\": false}. "
            "If YES, return: "
            "{"
            "\"is_food_review\": true, "
            "\"restaurant_name\": string or null, "
            "\"city\": string or null, "
            "\"rating\": one of [\"差评\",\"中评\",\"好评\"], "
            "\"quote\": one or a few key quoted sentences from subtitles"
            "}."
        ),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def extract_review(client: AzureOpenAI, title: str, subtitle_text: str) -> dict:
    messages = build_prompt(title, subtitle_text)
    resp = client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=messages,
        max_completion_tokens=800,
    )
    content = resp.choices[0].message.content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"is_food_review": False, "error": "invalid_json", "raw": content}


def main():
    if not AZURE_ENDPOINT or not AZURE_API_KEY:
        raise SystemExit("Missing AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_KEY.")
    if "://" not in AZURE_ENDPOINT:
        raise SystemExit("AZURE_OPENAI_ENDPOINT must be a full URL, e.g. https://<name>.openai.azure.com/")

    client = AzureOpenAI(
        api_version=API_VERSION,
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
    )
    print(f"Using Azure OpenAI endpoint: {AZURE_ENDPOINT}, deployment: {AZURE_DEPLOYMENT}")

    videos = load_json(INPUT_PATH, {})
    results = load_json(OUTPUT_PATH, {})
    index = load_json(INDEX_PATH, {})

    updated = 0
    total = len(videos)
    processed = 0
    for bvid, item in videos.items():
        processed += 1
        sig = compute_signature(item)
        if index.get(bvid) == sig and bvid in results:
            if processed % 10 == 0:
                print(f"progress: {processed}/{total} (cached)")
            continue

        title = item.get("title") or ""
        subtitle_text = item.get("subtitle_text") or ""
        if not subtitle_text.strip():
            results[bvid] = {"is_food_review": False, "reason": "empty_subtitle"}
            index[bvid] = sig
            updated += 1
            print(f"{bvid}: empty subtitle, skipped")
            continue

        try:
            print(f"{bvid}: analyzing ({len(subtitle_text)} chars)...")
            review = extract_review(client, title, subtitle_text)
        except Exception as exc:
            results[bvid] = {"is_food_review": False, "error": str(exc)}
            index[bvid] = sig
            updated += 1
            print(f"{bvid}: error {exc}")
            time.sleep(REQUEST_SLEEP_S)
            continue
        results[bvid] = review
        index[bvid] = sig
        updated += 1
        print(f"{bvid}: done -> {review.get('is_food_review')}")
        time.sleep(REQUEST_SLEEP_S)

    save_json(OUTPUT_PATH, results)
    save_json(INDEX_PATH, index)
    print(f"updated: {updated}, total: {len(results)}")


if __name__ == "__main__":
    main()
