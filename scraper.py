"""
Chewy Competitor Price Scraper (v2)
-----------------------------------
Selectors in this version were built from a real Chewy product page source
(hills-science-diet-adult-sensitive, dp/34037) supplied on 2026-07-02,
NOT from assumptions. Key facts learned from that page:

  - Product name lives in:  <h1 data-testid="product-title-heading">
    (the class name styles_productName__klctO is a build hash that changes,
    so we anchor on data-testid attributes, which are stable test hooks)
  - The buy box has two option cards:
        <div data-testid="buy-once-selector">   <- labeled "Buy once" (the one-time price)
        <div data-testid="autoship-selector">   <- the Autoship price (NEVER read this)
    The one-time price sits inside buy-once-selector at:
        span.kib-product-price__label  ->  "$89.99 Chewy Price"
  - Chewy's page does NOT say "one-time purchase" anywhere; it says "Buy once".
  - Stock status:  <span class="styles_inStockLabel__...">In Stock</span>
    (class hash varies, so we match any class containing "inStockLabel")
  - The JSON-LD structured data is a ProductGroup whose offers block only has
    lowPrice/highPrice ACROSS ALL BAG SIZES ($16.19-$179.98 on the sample page),
    so it is deliberately NOT used for price. It is only a name fallback.
  - Size ("30-lb bag") appears at the end of the product name after a comma,
    and in the selected size swatch (a.kib-swatch__text-swatch--selected).

If anything fails for a single product, that row is written as
"Error - Check Page" and the job continues. Historical data is never lost.
"""

import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 1. EDIT THIS LIST - paste your target Chewy product URLs between the quotes.
# ---------------------------------------------------------------------------
TARGET_URLS = [
    "https://www.chewy.com/hills-science-diet-adult-sensitive/dp/34037",
    "https://www.chewy.com/hills-science-diet-adult-sensitive/dp/120100",
]

RETAILER_NAME = "Chewy"
CSV_FILE = "data.csv"
CSV_COLUMNS = [
    "Date of Scrape",
    "Retailer",
    "Product Name",
    "Size",
    "Retail Price (One-Time)",
    "Status",
    "Product URL",
]

API_KEY = os.environ.get("SCRAPERAPI_KEY")
SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"

PRICE_PATTERN = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
SIZE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*-?\s*(?:oz|lb|lbs|g|kg|ml|ct|count|pack)\b", re.IGNORECASE
)


def fetch_page(url: str, render: bool = False) -> str:
    """Fetch through ScraperAPI. render=True runs JavaScript (more credits);
    the sample page shows the buy box is present in the plain HTML, so the
    cheap request should normally be enough."""
    params = {"api_key": API_KEY, "url": url, "country_code": "us"}
    if render:
        params["render"] = "true"
    response = requests.get(SCRAPERAPI_ENDPOINT, params=params, timeout=120)
    response.raise_for_status()
    return response.text


# --------------------------- field extractors ------------------------------

def extract_name(soup: BeautifulSoup) -> str | None:
    # Primary: stable test hook seen in live HTML
    h1 = soup.select_one('h1[data-testid="product-title-heading"]')
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    # Fallback 1: hashed class prefix
    h1 = soup.select_one('h1[class*="productName"]')
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    # Fallback 2: any h1
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    # Fallback 3: JSON-LD ProductGroup name (name only - never price, see header)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("Product", "ProductGroup"):
                if item.get("name"):
                    return str(item["name"]).strip()
    return None


def extract_one_time_price(soup: BeautifulSoup) -> str | None:
    """Read the price ONLY from inside the 'Buy once' card. The Autoship card
    (data-testid="autoship-selector") is a sibling and is never touched."""
    card = soup.select_one('[data-testid="buy-once-selector"]')
    if card is None:
        return None
    # Primary: the accessible price label, e.g. "$89.99 Chewy Price"
    label = card.select_one(".kib-product-price__label")
    if label:
        match = PRICE_PATTERN.search(label.get_text(" ", strip=True))
        if match:
            return f"${match.group(1)}"
    # Fallback: assemble from the dollars/cents spans in the same card
    dollars = card.select_one(".kib-product-price__dollars")
    cents = card.select_one(".kib-product-price__cents")
    if dollars and cents:
        return f"${dollars.get_text(strip=True)}.{cents.get_text(strip=True)}"
    # Last resort: any dollar amount within the buy-once card only
    match = PRICE_PATTERN.search(card.get_text(" ", strip=True))
    if match:
        return f"${match.group(1)}"
    return None


def extract_size(product_name: str | None, soup: BeautifulSoup) -> str:
    # Primary: size is the tail of the product name ("..., 30-lb bag")
    if product_name:
        tail = product_name.rsplit(",", 1)[-1].strip()
        if SIZE_PATTERN.search(tail):
            return tail
    # Fallback: the selected size swatch header (skips flavor swatches like
    # "Chicken" by requiring a number+unit match)
    for header in soup.select(
        ".kib-swatch__text-swatch--selected .kib-swatch__text-swatch-header"
    ):
        text = header.get_text(strip=True)
        if SIZE_PATTERN.search(text):
            return text
    # Last resort: any size-shaped token in the name
    if product_name:
        match = SIZE_PATTERN.search(product_name)
        if match:
            return match.group(0)
    return "Not found - Check Page"


def extract_status(soup: BeautifulSoup) -> str:
    """Primary hook verified on the live page: a span whose class contains
    'inStockLabel' with the text 'In Stock'. NOTE: the sample page was an
    in-stock product, so the out-of-stock branches below are best-guess
    fallbacks, not verified markup."""
    label = soup.select_one('span[class*="inStockLabel"]')
    if label:
        text = label.get_text(" ", strip=True).lower()
        if "out of stock" in text:
            return "Out of Stock"
        if "in stock" in text:
            return "In Stock"
    page_text = soup.get_text(" ", strip=True).lower()
    if "out of stock" in page_text or "currently unavailable" in page_text:
        return "Out of Stock"
    # The live page has add-to-cart buttons tagged with this tracking class
    if soup.select_one(".js-tracked-product-add-to-cart") or "add to cart" in page_text:
        return "In Stock"
    return "Error - Check Page"


# ------------------------------- assembly ----------------------------------

def parse_product(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    name = extract_name(soup)
    price = extract_one_time_price(soup)
    return {
        "Date of Scrape": date.today().isoformat(),
        "Retailer": RETAILER_NAME,
        "Product Name": name or "Error - Check Page",
        "Size": extract_size(name, soup),
        "Retail Price (One-Time)": price or "Error - Check Page",
        "Status": extract_status(soup),
        "Product URL": url,
    }


def error_row(url: str) -> dict:
    return {
        "Date of Scrape": date.today().isoformat(),
        "Retailer": RETAILER_NAME,
        "Product Name": "Error - Check Page",
        "Size": "Error - Check Page",
        "Retail Price (One-Time)": "Error - Check Page",
        "Status": "Error - Check Page",
        "Product URL": url,
    }


def scrape_one(url: str) -> dict:
    html = fetch_page(url, render=False)
    row = parse_product(html, url)
    if (
        row["Product Name"] == "Error - Check Page"
        or row["Retail Price (One-Time)"] == "Error - Check Page"
    ):
        print(f"  Plain fetch incomplete, retrying with JS rendering: {url}")
        html = fetch_page(url, render=True)
        row = parse_product(html, url)
    return row


def append_rows(rows: list[dict]) -> None:
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if not API_KEY:
        print("ERROR: SCRAPERAPI_KEY environment variable is not set.")
        print("Add it under Settings > Secrets and variables > Actions in GitHub.")
        sys.exit(1)

    print(f"Starting scrape at {datetime.now(timezone.utc).isoformat()} UTC")
    rows, failures = [], 0

    for i, url in enumerate(TARGET_URLS, start=1):
        print(f"[{i}/{len(TARGET_URLS)}] {url}")
        try:
            row = scrape_one(url)
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the job
            print(f"  FAILED ({type(exc).__name__}: {exc}) - writing error row")
            row = error_row(url)
        if row["Retail Price (One-Time)"] == "Error - Check Page":
            failures += 1
        rows.append(row)
        time.sleep(2)

    append_rows(rows)
    print(f"Done. Wrote {len(rows)} rows to {CSV_FILE} ({failures} with errors).")

    if failures == len(TARGET_URLS) and TARGET_URLS:
        print("All products failed - check your API key, credits, or the URLs.")
        sys.exit(1)


if __name__ == "__main__":
    main()
