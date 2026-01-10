#!/usr/bin/env python3

import argparse
import datetime as dt
import os
import sys
from typing import List, Dict

import yaml


def today_iso() -> str:
    return dt.date.today().isoformat()


def load_registry(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # suporta registry como lista direta ou como dict com chave "orgs"/"items"
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "orgs", "entries"):
            if key in data and isinstance(data[key], list):
                return data[key]

    raise ValueError("Registry format not supported")


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def generate_posts(items: List[Dict], out_dir: str):
    ensure_dir(out_dir)

    # ordenação estável para modal-id
    def sort_key(item):
        date = item.get("completed_on") or today_iso()
        name = item.get("name", "")
        return (date, name)

    items_sorted = sorted(items, key=sort_key)

    for idx, item in enumerate(items_sorted, start=1):
        name = item["name"]
        course_id = item["id"]
        title_human = item["title"]

        completed_on = item.get("completed_on") or today_iso()
        academic_area = item.get("academic_area")
        site_hero_image = item.get("site_hero_image")
        site_description = item.get("site_description", "")

        filename = f"{completed_on}-{name}.markdown"
        filepath = os.path.join(out_dir, filename)

        front_matter = f"""---
title: "{course_id} {title_human}"
link: "/{name}"
category: {academic_area}
layout: default
modal-id: {idx}
date: {completed_on}
img: {site_hero_image}
alt: image-alt
description: {site_description or ""}
---
"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(front_matter)

        print(f"✔ generated {filepath}")


def main():
    parser = argparse.ArgumentParser(description="Generate Jekyll posts from registry")
    parser.add_argument("--registry", required=True, help="Path to registry YAML")
    parser.add_argument("--out-posts", required=True, help="Path to _posts directory")

    args = parser.parse_args()

    items = load_registry(args.registry)
    generate_posts(items, args.out_posts)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)