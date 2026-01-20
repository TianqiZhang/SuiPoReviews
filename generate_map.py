#!/usr/bin/env python3
"""Generate a China map with city markers showing visit counts."""

import json
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# City coordinates (lon, lat)
CITY_COORDS = {
    "北京": (116.4, 39.9),
    "上海": (121.5, 31.2),
    "广州": (113.3, 23.1),
    "深圳": (114.1, 22.5),
    "成都": (104.1, 30.7),
    "重庆": (106.5, 29.5),
    "杭州": (120.2, 30.3),
    "西安": (108.9, 34.3),
    "天津": (117.2, 39.1),
    "南宁": (108.3, 22.8),
    "哈尔滨": (126.6, 45.8),
    "济南": (117.0, 36.7),
    "郑州": (113.6, 34.8),
    "洛阳": (112.4, 34.6),
    "开封": (114.3, 34.8),
    "齐齐哈尔": (124.0, 47.3),
    "烟台": (121.4, 37.5),
    "邯郸": (114.5, 36.6),
    "广西钦州": (108.6, 21.9),
}

REVIEWS_PATH = Path("data/video_reviews.json")
OUTPUT_PATH = Path("assets/china_map.svg")
GEOJSON_PATH = Path("assets/china.geojson")

# China GeoJSON source (simplified)
CHINA_GEOJSON_URL = "https://raw.githubusercontent.com/yezongyang/china-bindary-bindbindbindbindbindbindbindary/bindmastbinderbindbind/bindchinbindabind.bindgeojsonbind"


def download_china_geojson():
    """Download China GeoJSON if not exists."""
    if GEOJSON_PATH.exists():
        return json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    # Use embedded simplified China boundary
    # This is a simplified version of China's boundary for visualization
    return None


def load_city_counts() -> Counter:
    if not REVIEWS_PATH.exists():
        return Counter()
    reviews = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    cities = []
    for review in reviews.values():
        if review.get("is_food_review") and review.get("city"):
            cities.append(review["city"])
    return Counter(cities)


def plot_china_provinces(ax):
    """Plot China map using province boundaries from local GeoJSON."""
    if not GEOJSON_PATH.exists():
        print(f"Warning: {GEOJSON_PATH} not found. Run with --download first.")
        # Draw a simple rectangle as placeholder
        ax.add_patch(mpatches.Rectangle((73, 18), 62, 35, fill=True,
                                         facecolor="#fff9e6", edgecolor="#cccccc", linewidth=1))
        return

    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    for feature in geojson.get("features", []):
        geom = feature.get("geometry", {})
        geom_type = geom.get("type")
        coords = geom.get("coordinates", [])

        if geom_type == "Polygon":
            for ring in coords:
                xs, ys = zip(*ring)
                ax.fill(xs, ys, facecolor="#fff9e6", edgecolor="#cccccc", linewidth=0.5)
        elif geom_type == "MultiPolygon":
            for polygon in coords:
                for ring in polygon:
                    if len(ring) > 2:
                        xs, ys = zip(*ring)
                        ax.fill(xs, ys, facecolor="#fff9e6", edgecolor="#cccccc", linewidth=0.5)


def generate_map(city_counts: Counter):
    fig, ax = plt.subplots(figsize=(12, 10), facecolor="#f0f4f8")
    ax.set_facecolor("#d4e5f7")

    # Plot China
    plot_china_provinces(ax)

    # Plot cities
    for city, count in city_counts.items():
        if city not in CITY_COORDS:
            print(f"Warning: coordinates not found for {city}")
            continue
        lon, lat = CITY_COORDS[city]
        # Marker size based on count
        size = 100 + count * 50
        ax.scatter(lon, lat, s=size, c="#e74c3c", alpha=0.75,
                   edgecolors="#c0392b", linewidth=1.5, zorder=5)
        # Label
        ax.annotate(
            f"{city} ({count})",
            (lon, lat),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color="#2c3e50",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.7, edgecolor="none"),
            zorder=6,
        )

    # Set limits
    ax.set_xlim(73, 136)
    ax.set_ylim(17, 54)
    ax.set_aspect("equal")
    ax.axis("off")

    # Title
    total = sum(city_counts.values())
    ax.set_title(
        f"特厨隋坡探店地图\n{len(city_counts)} 个城市 · {total} 家餐厅",
        fontsize=16, fontweight="bold", color="#2c3e50", pad=15
    )

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUTPUT_PATH, format="svg", bbox_inches="tight", dpi=150)
    plt.savefig(OUTPUT_PATH.with_suffix(".png"), format="png", bbox_inches="tight", dpi=150)
    plt.close()
    print(f"Map saved to {OUTPUT_PATH} and {OUTPUT_PATH.with_suffix('.png')}")


def download_geojson():
    """Download China provinces GeoJSON from GitHub."""
    # Try multiple sources
    urls = [
        "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json",
        "https://raw.githubusercontent.com/nicegoodxs/china-bindbindgebindojsonbindbind/bindmabindsterbind/bindchinabind.bindgeojonbindsonbind",
    ]

    GEOJSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    for url in urls:
        try:
            print(f"Trying {url}...")
            with urlopen(url, timeout=10) as response:
                data = response.read().decode("utf-8")
            GEOJSON_PATH.write_text(data, encoding="utf-8")
            print(f"Saved to {GEOJSON_PATH}")
            return
        except Exception as e:
            print(f"Failed: {e}")
            continue

    print("All sources failed. Please download manually.")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="Download China GeoJSON data")
    args = parser.parse_args()

    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    if args.download:
        download_geojson()
        return

    city_counts = load_city_counts()
    if not city_counts:
        print("No city data found.")
        return

    print(f"Found {len(city_counts)} cities: {dict(city_counts)}")
    generate_map(city_counts)


if __name__ == "__main__":
    main()
