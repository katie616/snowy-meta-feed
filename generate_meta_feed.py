#!/usr/bin/env python3
"""Generate a Meta Commerce Manager product catalogue feed from Homhero listings.

This script is designed for GitHub Actions. It reads the Homhero API key from the
HOMHERO_API_KEY environment variable, fetches listings from Homhero, converts them
into the Meta product catalogue template, and writes a public CSV to docs/ so it
can be served by GitHub Pages.
"""

from __future__ import annotations

import csv
import html
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urljoin

import requests

API_BASE = os.environ.get("HOMHERO_API_BASE", "https://api.homhero.com.au").rstrip("/")
BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "https://snowymountainsaccommodation.au/accommodation/{slug}/")
BRAND_NAME = os.environ.get("BRAND_NAME", "Snowy Mountains Accommodation")
PLACEHOLDER_PRICE_AMOUNT = os.environ.get("PLACEHOLDER_PRICE_AMOUNT", "1.00").strip()
META_FEED_CURRENCY = os.environ.get("META_FEED_CURRENCY", "AUD").strip().upper()
# Meta's product-feed specification expects price as: number + space + ISO 4217 currency code,
# for example "10.00 USD". Keep PLACEHOLDER_PRICE as an override for backwards compatibility,
# but prefer PLACEHOLDER_PRICE_AMOUNT + META_FEED_CURRENCY so the shopfront currency can be changed
# from GitHub Actions without editing code if Meta reports a dominant-currency mismatch.
PLACEHOLDER_PRICE = os.environ.get("PLACEHOLDER_PRICE", f"{PLACEHOLDER_PRICE_AMOUNT} {META_FEED_CURRENCY}").strip()
DEFAULT_QUANTITY = os.environ.get("DEFAULT_QUANTITY", "999")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "8"))

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "docs"
OUTPUT_FILE = OUTPUT_DIR / "snowy_mountains_meta_products.csv"
DIAGNOSTICS_FILE = OUTPUT_DIR / "snowy_mountains_meta_products_diagnostics.csv"

META_PRODUCT_FIELDS = [
    "id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "brand",
    "google_product_category",
    "fb_product_category",
    "quantity_to_sell_on_facebook",
    "sale_price",
    "sale_price_effective_date",
    "item_group_id",
    "gender",
    "color",
    "size",
    "age_group",
    "material",
    "pattern",
    "shipping",
    "shipping_weight",
    "offer_disclaimer",
    "offer_disclaimer_url",
    "video[0].url",
    "video[0].tag[0]",
    "gtin",
    "product_tags[0]",
    "product_tags[1]",
    "style[0]",
]

REQUIRED_FIELDS = [
    "id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "quantity_to_sell_on_facebook",
]


def clean_text(value: Any, max_len: int | None = None) -> str:
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if max_len and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def first_present(data: Dict[str, Any], keys: Iterable[str], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def extract_image_url(listing: Dict[str, Any]) -> str:
    images = first_present(listing, ["images", "photos", "gallery"], [])
    if isinstance(images, list):
        ordered = sorted(
            [img for img in images if isinstance(img, dict)],
            key=lambda x: x.get("display_order", 999999),
        )
        for img in ordered:
            url = first_present(img, ["full", "url", "src", "image_url", "large", "original", "thumb"], "")
            if isinstance(url, str) and url.startswith("http"):
                return url
        for img in images:
            if isinstance(img, str) and img.startswith("http"):
                return img
    if isinstance(images, dict):
        url = first_present(images, ["full", "url", "src", "image_url"], "")
        if isinstance(url, str) and url.startswith("http"):
            return url
    direct = first_present(listing, ["image", "image_url", "hero_image", "thumbnail", "photo"], "")
    return direct if isinstance(direct, str) and direct.startswith("http") else ""


def build_listing_url(slug: str) -> str:
    if "{slug}" in BOOKING_BASE_URL:
        return BOOKING_BASE_URL.replace("{slug}", slug)
    return urljoin(BOOKING_BASE_URL.rstrip("/") + "/", slug)


def request_json(path: str, key: str) -> Dict[str, Any]:
    response = requests.get(
        API_BASE + path,
        headers={"Authorization": key, "Accept": "application/json"},
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected non-object JSON for {path}")
    return payload


def fetch_summaries(key: str) -> List[Dict[str, Any]]:
    payload = request_json("/listings", key)
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        raise ValueError("Homhero /listings did not return a listings array")
    return [item for item in listings if isinstance(item, dict)]


def fetch_detail(summary: Dict[str, Any], key: str) -> Tuple[Dict[str, Any], str]:
    slug = first_present(summary, ["slug", "account_listing_slug", "listing_slug"])
    if not slug:
        return summary, "missing slug; used summary only"
    try:
        payload = request_json(f"/listing/{slug}", key)
        detail = payload.get("listing") if isinstance(payload.get("listing"), dict) else payload.get("data")
        if not isinstance(detail, dict):
            detail = payload
        merged = dict(summary)
        merged.update(detail)
        return merged, ""
    except Exception as exc:  # Continue so one slow/broken listing does not stop the whole feed.
        return summary, f"detail fetch failed; used summary only: {type(exc).__name__}: {str(exc)[:140]}"


def to_product_row(listing: Dict[str, Any]) -> Dict[str, str]:
    slug = clean_text(first_present(listing, ["slug", "account_listing_slug", "listing_slug", "id"]))
    title = clean_text(first_present(listing, ["name", "title", "listing_name"], "Untitled Listing"), 200)
    description = clean_text(first_present(listing, ["description", "short_description", "summary"], title), 5000)
    suburb = clean_text(first_present(listing, ["suburb", "neighborhood", "neighbourhood", "region", "area"], "Snowy Mountains"), 100)
    category_raw = first_present(listing, ["property_type", "type"], "Accommodation")
    if isinstance(listing.get("categories_list"), list) and listing["categories_list"]:
        category_raw = listing["categories_list"][0]

    row = {field: "" for field in META_PRODUCT_FIELDS}
    row.update(
        {
            "id": clean_text(first_present(listing, ["id", "listing_id", "rms_id", "slug"], slug), 100),
            "title": title,
            "description": description,
            "availability": "in stock",
            "condition": "new",
            "price": PLACEHOLDER_PRICE,
            "link": build_listing_url(slug),
            "image_link": extract_image_url(listing),
            "brand": BRAND_NAME,
            "google_product_category": "",
            "fb_product_category": "",
            "quantity_to_sell_on_facebook": DEFAULT_QUANTITY,
            "product_tags[0]": "holiday_rental",
            "product_tags[1]": suburb,
            "style[0]": clean_text(category_raw, 100),
        }
    )
    return row


def validate(row: Dict[str, str]) -> List[str]:
    missing = [field for field in REQUIRED_FIELDS if not row.get(field)]
    price = row.get("price", "").strip()
    if not re.match(r"^[0-9]+(?:\.[0-9]{2})\s+[A-Z]{3}$", price):
        missing.append("valid_price_with_iso_currency")
    if re.match(r"^0+(?:\.00)?\s+[A-Z]{3}$", price):
        missing.append("positive_price")
    quantity = row.get("quantity_to_sell_on_facebook", "")
    if not quantity.isdigit() or int(quantity) < 1:
        missing.append("valid_quantity")
    return missing


def main() -> int:
    key = os.environ.get("HOMHERO_API_KEY", "").strip()
    if not key:
        print("Missing HOMHERO_API_KEY. Add it as a GitHub Actions secret.", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summaries = fetch_summaries(key)
    print(f"Fetched {len(summaries)} listing summaries from Homhero")

    details: List[Tuple[Dict[str, Any], str]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_detail, summary, key) for summary in summaries]
        for index, future in enumerate(as_completed(futures), 1):
            details.append(future.result())
            if index % 10 == 0 or index == len(futures):
                print(f"Processed {index}/{len(futures)} listing details")

    rows: List[Dict[str, str]] = []
    diagnostics: List[Dict[str, str]] = []
    for listing, fetch_note in details:
        row = to_product_row(listing)
        rows.append(row)
        diagnostics.append(
            {
                "id": row["id"],
                "title": row["title"],
                "link": row["link"],
                "missing_or_invalid_fields": "; ".join(validate(row)),
                "price": row["price"],
                "currency_code": row["price"].split()[-1] if row["price"].split() else "",
                "quantity_to_sell_on_facebook": row["quantity_to_sell_on_facebook"],
                "fetch_note": fetch_note,
            }
        )

    rows.sort(key=lambda r: r["title"].lower())
    diagnostics.sort(key=lambda r: r["title"].lower())

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=META_PRODUCT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    with DIAGNOSTICS_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "title", "link", "missing_or_invalid_fields", "price", "currency_code", "quantity_to_sell_on_facebook", "fetch_note"],
        )
        writer.writeheader()
        writer.writerows(diagnostics)

    issue_count = sum(1 for item in diagnostics if item["missing_or_invalid_fields"])
    print(f"Rows written: {len(rows)}")
    print(f"Rows with missing/invalid required fields: {issue_count}")
    print(f"Feed written to: {OUTPUT_FILE}")
    print(f"Diagnostics written to: {DIAGNOSTICS_FILE}")
    return 0 if issue_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
