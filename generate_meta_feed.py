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
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urljoin

import requests

API_BASE = os.environ.get("HOMHERO_API_BASE", "https://api.homhero.com.au").rstrip("/")
BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "https://snowymountainsaccommodation.au/accommodation/{slug}/")
BRAND_NAME = os.environ.get("BRAND_NAME", "Snowy Mountains Accommodation")
META_FEED_CURRENCY = os.environ.get("META_FEED_CURRENCY", "AUD").strip().upper()
# Default safety-net price used only when neither Homhero nor the public listing page exposes
# a usable per-property from-price. PLACEHOLDER_PRICE_AMOUNT remains supported for backwards
# compatibility, but META_FEED_FALLBACK_PRICE is the preferred setting name.
META_FEED_FALLBACK_PRICE = os.environ.get(
    "META_FEED_FALLBACK_PRICE",
    os.environ.get("PLACEHOLDER_PRICE_AMOUNT", "308.00"),
).strip()
PLACEHOLDER_PRICE_AMOUNT = META_FEED_FALLBACK_PRICE
USE_HOMHERO_STARTING_PRICE = os.environ.get("USE_HOMHERO_STARTING_PRICE", "true").strip().lower() in {"1", "true", "yes", "y", "on"}
USE_WEBSITE_FROM_PRICE = os.environ.get("USE_WEBSITE_FROM_PRICE", "true").strip().lower() in {"1", "true", "yes", "y", "on"}
ADD_STARTING_PRICE_TO_DESCRIPTION = os.environ.get("ADD_STARTING_PRICE_TO_DESCRIPTION", "true").strip().lower() in {"1", "true", "yes", "y", "on"}
STARTING_PRICE_LABEL = os.environ.get("STARTING_PRICE_LABEL", "Rates from AU${amount} per night. Final price depends on dates, guests and availability.").strip()
META_FEED_PRICE_OVERRIDES = os.environ.get("META_FEED_PRICE_OVERRIDES", "").strip()
WEBSITE_FROM_PRICE_TIMEOUT = float(os.environ.get("WEBSITE_FROM_PRICE_TIMEOUT", "12"))
# Meta's product-feed specification expects price as: number + space + ISO 4217 currency code,
# for example "308.00 AUD". Keep PLACEHOLDER_PRICE as an override for backwards compatibility,
# but prefer META_FEED_FALLBACK_PRICE + META_FEED_CURRENCY so the safety-net amount and shopfront
# currency can be changed from GitHub Actions without editing code.
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

PRICE_FIELD_CANDIDATES = {
    "starting_price",
    "start_price",
    "from_price",
    "price_from",
    "minimum_price",
    "min_price",
    "lowest_price",
    "cheapest_price",
    "base_price",
    "public_price",
    "display_price",
    "default_price",
    "minimum_nightly_price",
    "min_nightly_price",
    "nightly_price",
    "nightly_rate",
    "base_rate",
    "base_nightly_rate",
    "from_rate",
    "lowest_rate",
    "minimum_rate",
    "min_rate",
    "rate_from",
    "rack_rate",
    "standard_rate",
    "tariff",
}

PRICE_FIELD_EXCLUSIONS = {
    "bond",
    "deposit",
    "fee",
    "fees",
    "cleaning",
    "linen",
    "service",
    "tax",
    "taxes",
    "commission",
    "discount",
    "surcharge",
    "security",
    "pet",
    "extra",
    "total",
    "count",
    "quantity",
    "rating",
}


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


def normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def field_is_allowed_price_candidate(key: str) -> bool:
    normalised = normalise_key(key)
    if not normalised:
        return False
    if any(part in normalised for part in PRICE_FIELD_EXCLUSIONS):
        return False
    return normalised in PRICE_FIELD_CANDIDATES


def parse_price_amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float, Decimal)):
        candidate = str(value)
    else:
        candidate = str(value)
    match = re.search(r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+)", candidate.replace("AUD", ""))
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    # Ignore tiny non-accommodation values that are likely counts or placeholder defaults.
    if amount < Decimal("10"):
        return None
    return amount.quantize(Decimal("0.01"))


def normalise_lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def parse_price_overrides() -> Dict[str, Decimal]:
    if not META_FEED_PRICE_OVERRIDES:
        return {}
    try:
        raw = json.loads(META_FEED_PRICE_OVERRIDES)
    except json.JSONDecodeError as exc:
        print(f"Ignoring invalid META_FEED_PRICE_OVERRIDES JSON: {exc}", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print("Ignoring META_FEED_PRICE_OVERRIDES because it is not a JSON object", file=sys.stderr)
        return {}
    overrides: Dict[str, Decimal] = {}
    for key, value in raw.items():
        amount = parse_price_amount(value)
        if amount is not None:
            overrides[normalise_lookup_key(key)] = amount
    return overrides


PRICE_OVERRIDES = parse_price_overrides()


def listing_lookup_keys(listing: Dict[str, Any], slug: str, title: str, link: str) -> List[str]:
    values = [
        first_present(listing, ["id", "listing_id", "rms_id"], ""),
        slug,
        title,
        link.rstrip("/").split("/")[-1] if link else "",
    ]
    keys: List[str] = []
    for value in values:
        key = normalise_lookup_key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def extract_override_price(listing: Dict[str, Any], slug: str, title: str, link: str) -> Tuple[Decimal | None, str]:
    for key in listing_lookup_keys(listing, slug, title, link):
        if key in PRICE_OVERRIDES:
            return PRICE_OVERRIDES[key], f"manual_price_override:{key}"
    return None, ""


def extract_website_from_price(link: str) -> Tuple[Decimal | None, str]:
    if not USE_WEBSITE_FROM_PRICE:
        return None, "website_from_price_disabled"
    if not link:
        return None, "website_from_price_missing_link"
    try:
        response = requests.get(link, headers={"Accept": "text/html"}, timeout=(5, WEBSITE_FROM_PRICE_TIMEOUT))
        response.raise_for_status()
    except Exception as exc:
        return None, f"website_from_price_fetch_failed:{type(exc).__name__}"

    page_text = clean_text(response.text)
    patterns = [
        r"From\s*\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*/\s*night\s+based\s+on\s+a\s+7\s+night\s+stay",
        r"From\s*\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*/\s*night",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if match:
            amount = parse_price_amount(match.group(1))
            if amount is not None:
                return amount, "website_from_price_7_night" if "7" in pattern else "website_from_price"
    return None, "website_from_price_not_found"


def fallback_price_amount() -> Decimal:
    amount = parse_price_amount(META_FEED_FALLBACK_PRICE)
    if amount is None:
        print(f"Invalid META_FEED_FALLBACK_PRICE {META_FEED_FALLBACK_PRICE!r}; using 308.00", file=sys.stderr)
        return Decimal("308.00")
    return amount


def iter_price_candidates(value: Any, path: str = "") -> Iterable[Tuple[Decimal, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            current_path = f"{path}.{key}" if path else str(key)
            if field_is_allowed_price_candidate(str(key)):
                amount = parse_price_amount(nested)
                if amount is not None:
                    yield amount, current_path
            if isinstance(nested, (dict, list)):
                yield from iter_price_candidates(nested, current_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_price_candidates(item, f"{path}[{index}]")


def extract_starting_price(listing: Dict[str, Any]) -> Tuple[Decimal | None, str]:
    if not USE_HOMHERO_STARTING_PRICE:
        return None, "placeholder_price_disabled"
    candidates = list(iter_price_candidates(listing))
    if not candidates:
        return None, "placeholder_price_no_supported_api_price"
    amount, source = min(candidates, key=lambda item: item[0])
    return amount, f"api_starting_price:{source}"


def format_meta_price(amount: Decimal | None) -> str:
    if amount is None:
        return PLACEHOLDER_PRICE
    return f"{amount:.2f} {META_FEED_CURRENCY}"


def append_starting_price_text(description: str, amount: Decimal | None) -> str:
    if not ADD_STARTING_PRICE_TO_DESCRIPTION or amount is None:
        return description
    label = STARTING_PRICE_LABEL.replace("{amount}", f"{amount:.0f}").replace("{amount_2dp}", f"{amount:.2f}")
    if label and label.lower() not in description.lower():
        combined = f"{label} {description}".strip()
        return clean_text(combined, 5000)
    return description


def to_product_row(listing: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
    slug = clean_text(first_present(listing, ["slug", "account_listing_slug", "listing_slug", "id"]))
    title = clean_text(first_present(listing, ["name", "title", "listing_name"], "Untitled Listing"), 200)
    description = clean_text(first_present(listing, ["description", "short_description", "summary"], title), 5000)
    suburb = clean_text(first_present(listing, ["suburb", "neighborhood", "neighbourhood", "region", "area"], "Snowy Mountains"), 100)
    category_raw = first_present(listing, ["property_type", "type"], "Accommodation")
    if isinstance(listing.get("categories_list"), list) and listing["categories_list"]:
        category_raw = listing["categories_list"][0]

    link = build_listing_url(slug)
    starting_price_amount, pricing_note = extract_starting_price(listing)
    if starting_price_amount is None:
        starting_price_amount, pricing_note = extract_override_price(listing, slug, title, link)
    if starting_price_amount is None:
        starting_price_amount, pricing_note = extract_website_from_price(link)
    if starting_price_amount is None:
        starting_price_amount = fallback_price_amount()
        pricing_note = f"default_fallback_from_price:{META_FEED_FALLBACK_PRICE}"
    description = append_starting_price_text(description, starting_price_amount)

    row = {field: "" for field in META_PRODUCT_FIELDS}
    row.update(
        {
            "id": clean_text(first_present(listing, ["id", "listing_id", "rms_id", "slug"], slug), 100),
            "title": title,
            "description": description,
            "availability": "in stock",
            "condition": "new",
            "price": format_meta_price(starting_price_amount),
            "link": link,
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
    return row, pricing_note


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
        row, pricing_note = to_product_row(listing)
        rows.append(row)
        diagnostics.append(
            {
                "id": row["id"],
                "title": row["title"],
                "link": row["link"],
                "missing_or_invalid_fields": "; ".join(validate(row)),
                "price": row["price"],
                "currency_code": row["price"].split()[-1] if row["price"].split() else "",
                "pricing_note": pricing_note,
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

    diagnostic_fields = [
        "id",
        "title",
        "link",
        "missing_or_invalid_fields",
        "price",
        "currency_code",
        "pricing_note",
        "quantity_to_sell_on_facebook",
        "fetch_note",
    ]
    with DIAGNOSTICS_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=diagnostic_fields)
        writer.writeheader()
        writer.writerows(diagnostics)

    issue_count = sum(1 for item in diagnostics if item["missing_or_invalid_fields"])
    api_price_count = sum(1 for item in diagnostics if item["pricing_note"].startswith("api_starting_price:"))
    print(f"Rows written: {len(rows)}")
    website_price_count = sum(1 for item in diagnostics if item["pricing_note"].startswith("website_from_price"))
    override_price_count = sum(1 for item in diagnostics if item["pricing_note"].startswith("manual_price_override:"))
    default_fallback_count = sum(1 for item in diagnostics if item["pricing_note"].startswith("default_fallback_from_price:"))
    print(f"Rows using Homhero API starting prices: {api_price_count}")
    print(f"Rows using website 7-night from-prices: {website_price_count}")
    print(f"Rows using manual price overrides: {override_price_count}")
    print(f"Rows using default fallback from-price: {default_fallback_count}")
    print(f"Rows with missing/invalid required fields: {issue_count}")
    print(f"Feed written to: {OUTPUT_FILE}")
    print(f"Diagnostics written to: {DIAGNOSTICS_FILE}")
    return 0 if issue_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
