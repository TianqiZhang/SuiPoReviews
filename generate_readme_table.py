#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


README_PATH = Path("README.md")
REVIEWS_PATH = Path("data/video_reviews.json")
SUMMARY_PATH = Path("data/videos_summary.json")

START_MARKER = "<!-- REVIEWS_TABLE_START -->"
END_MARKER = "<!-- REVIEWS_TABLE_END -->"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_cell(text):
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


RATING_ORDER = {"好评": 0, "中评": 1, "差评": 2}


def build_rows(reviews: dict, summary: dict, include_all: bool):
    rows = []
    for bvid, review in reviews.items():
        is_food = bool(review.get("is_food_review"))
        if not include_all and not is_food:
            continue
        meta = summary.get(bvid, {})
        title = meta.get("title") or bvid
        url = f"https://www.bilibili.com/video/{bvid}"
        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "url": url,
                "is_food_review": "是" if is_food else "否",
                "restaurant_name": review.get("restaurant_name"),
                "city": review.get("city"),
                "rating": review.get("rating"),
                "quote": ("（摘述待处理）" if review.get("summary_status") == "pending" else
                          review.get("review_summary", review.get("quote"))),
                "pubdate": int(meta.get("pubdate") or 0),
            }
        )
    # Sort by rating (好评 < 中评 < 差评), then by city
    rows.sort(key=lambda r: (
        RATING_ORDER.get(r["rating"], 99),
        r["city"] or "",
    ))
    return rows


def render_table(rows):
    header = (
        "| 标题 | 是否探店 | 餐厅 | 城市 | 评分 | 评价摘述 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )
    body_lines = []
    for r in rows:
        title = sanitize_cell(r["title"])
        link = f"[{title}]({r['url']})"
        body_lines.append(
            "| {title} | {is_food} | {restaurant} | {city} | {rating} | {quote} |".format(
                title=link,
                is_food=sanitize_cell(r["is_food_review"]),
                restaurant=sanitize_cell(r["restaurant_name"]),
                city=sanitize_cell(r["city"]),
                rating=sanitize_cell(r["rating"]),
                quote=sanitize_cell(r["quote"]),
            )
        )
    if not body_lines:
        body_lines = ["| （无匹配结果） |  |  |  |  |  |"]
    return header + "\n".join(body_lines)


def replace_section(readme_text: str, table_text: str) -> str:
    if START_MARKER not in readme_text or END_MARKER not in readme_text:
        raise SystemExit("README missing markers for review table.")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        flags=re.DOTALL,
    )
    replacement = f"{START_MARKER}\n{table_text}\n{END_MARKER}"
    return pattern.sub(replacement, readme_text, count=1)


def main():
    parser = argparse.ArgumentParser(description="Generate README review table.")
    parser.add_argument("--all", action="store_true", help="Include non-review videos.")
    args = parser.parse_args()

    reviews = load_json(REVIEWS_PATH, {})
    summary = load_json(SUMMARY_PATH, {})

    rows = build_rows(reviews, summary, include_all=args.all)
    table = render_table(rows)

    readme_text = README_PATH.read_text(encoding="utf-8")
    updated = replace_section(readme_text, table)
    README_PATH.write_text(updated, encoding="utf-8")
    print(f"updated README table with {len(rows)} rows")


if __name__ == "__main__":
    main()
