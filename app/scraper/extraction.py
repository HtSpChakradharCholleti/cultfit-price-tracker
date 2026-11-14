"""
Cult.fit price page extraction helpers.

Strategy
--------
The page is a Next.js App Router app that embeds its RSC payload as a series of
``self.__next_f.push([1, "<json_string>"])`` ``<script>`` blocks in the HTML.

The plan cards live inside one or more of those blocks.  Rather than relying on
a fixed block index (which can change with deploys) we scan **every** block and
collect every heading+price pair we can find, using the nearest preceding
``widgetName`` to infer the membership tier.
"""

import json
import logging
import re
import time
from typing import Optional

import httpx

from app.config import SCRAPER_TIMEOUT, USER_AGENT
from app.models.plan_offer import OfferData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser-like headers
# ---------------------------------------------------------------------------
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
# Matches each self.__next_f.push([1, <payload>]) block.
# The payload is a JSON string (possibly multi-line, hence re.DOTALL).
_RE_NEXT_PUSH = re.compile(
    r"self\.__next_f\.push\(\s*\[1\s*,\s*(?P<payload>\"(?:[^\"\\]|\\.)*\")\s*\]\s*\)",
    re.DOTALL,
)

# Within a decoded JSON text block:
_RE_HEADING = re.compile(
    r'"heading"\s*:\s*"(\d+)"\s*,\s*"subHeading"\s*:\s*"([^"]+)"'
)
_RE_PRICE = re.compile(
    r'"mrp"\s*:\s*"([\d,]+)"\s*,\s*"listingPrice"\s*:\s*"([\d,]+)"'
)
_RE_WIDGET_NAME = re.compile(r'"widgetName"\s*:\s*"([^"]+)"')
_RE_OFFER_STRING = re.compile(r'"offerString"\s*:\s*"([^"]*)"')
_RE_PRODUCT_ID = re.compile(r'"productId"\s*:\s*"([^"]+)"')
_RE_ACTION_PRODUCT_ID = re.compile(r"(?:[?&]productId=)([^&\"\\\\]+)")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_page(url: str, max_retries: int = 4) -> str:
    """
    HTTP GET *url* with browser-like headers.

    Automatically retries if Cloudflare's dynamic upstream serves the reduced
    'Non Plus Cities' fallback instead of the complete Metro pricing payload.

    Raises
    ------
    RuntimeError
        If Cloudflare (or similar bot detection) intercepts the request.
    httpx.HTTPStatusError
        On a non-2xx response.
    """
    for attempt in range(max_retries):
        with httpx.Client(
            timeout=SCRAPER_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        html = response.text

        # Cloudflare / bot-detection guard
        if len(html) < 1000 or "cf-browser-verification" in html:
            raise RuntimeError(
                f"Cloudflare or bot-detection page returned (len={len(html)}). "
                "Cannot parse pricing data."
            )

        # If we got the complete Metro payload (length > 500k or contains CPE widget), return immediately
        if "cpe" in html.lower() or len(html) > 500000 or attempt == max_retries - 1:
            logger.debug("Fetched %s — %d chars (attempt %d)", url, len(html), attempt + 1)
            return html

        time.sleep(0.2)

    return html


def parse_plans(html: str) -> list[dict]:
    """
    Extract all membership plan cards from the Next.js embedded JSON payload.

    Returns a list of dicts with keys:
        tier, name, duration_months, duration_label, mrp, selling_price,
        offer_string, product_id, widget_name
    """
    decoded_blocks: list[str] = []
    for match in _RE_NEXT_PUSH.finditer(html):
        try:
            decoded_blocks.append(json.loads(match.group("payload")))
        except (json.JSONDecodeError, ValueError):
            continue

    structured = _parse_structured_plans(decoded_blocks)
    if structured:
        logger.info("Extracted %d structured plan(s) from page", len(structured))
        return structured

    plans: list[dict] = []
    seen: set[tuple] = set()   # for deduplication

    for inner_text in decoded_blocks:

        # Find all widgetName occurrences and their positions so we can
        # attribute each plan card to the nearest preceding widget.
        widget_positions: list[tuple[int, str]] = [
            (m.start(), m.group(1))
            for m in _RE_WIDGET_NAME.finditer(inner_text)
        ]

        heading_matches = list(_RE_HEADING.finditer(inner_text))
        if not heading_matches:
            continue

        for h_match in heading_matches:
            heading_val = h_match.group(1)      # e.g. "12"
            sub_heading  = h_match.group(2)     # e.g. "MONTHS" or "Months all India"

            # Try to parse duration
            try:
                duration_months = int(heading_val)
            except ValueError:
                continue
            if duration_months <= 0:
                continue

            # Find the nearest price block *after* this heading
            pos_after_heading = h_match.end()
            price_match = _RE_PRICE.search(inner_text, pos_after_heading)
            if price_match is None:
                continue

            mrp = normalize_price(price_match.group(1))
            selling_price = normalize_price(price_match.group(2))
            if mrp is None or selling_price is None:
                continue

            # Look for offerString and productId near the price block
            offer_string = ""
            offer_m = _RE_OFFER_STRING.search(
                inner_text,
                h_match.start(),
                price_match.end() + 500,
            )
            if offer_m:
                offer_string = offer_m.group(1)

            product_id_code = ""
            pid_m = _RE_PRODUCT_ID.search(
                inner_text,
                h_match.start(),
                price_match.end() + 500,
            )
            if pid_m:
                product_id_code = pid_m.group(1)
            if not product_id_code:
                action_m = _RE_ACTION_PRODUCT_ID.search(
                    inner_text[h_match.start():price_match.end() + 500]
                )
                if action_m:
                    product_id_code = action_m.group(1)

            # Determine tier from the nearest preceding widgetName
            tier = "ELITE"  # safe default
            widget_name = ""
            nearest_pos = -1
            for w_pos, w_name in widget_positions:
                if w_pos <= h_match.start() and w_pos > nearest_pos:
                    nearest_pos = w_pos
                    widget_name = w_name

            wl = widget_name.lower()
            if "non plus" in wl or "non-plus" in wl:
                tier = "ELITE (NON-METRO)" if "pro" not in wl else "PRO (NON-METRO)"
            elif "elite plus" in wl or "elite+" in wl:
                tier = "ELITE PLUS"
            elif "pro plus" in wl or "pro+" in wl:
                tier = "PRO PLUS"
            elif "play" in wl:
                tier = "PLAY"
            elif "cpp" in wl or ("pro" in wl and "elite" not in wl):
                tier = "PRO"
            elif "cpe" in wl or "elite" in wl:
                tier = "ELITE"

            # Build a human-readable plan name
            sub_upper = sub_heading.upper()
            if "ALL INDIA" in sub_upper:
                duration_label = f"{duration_months}M All India"
                plan_name = f"{tier} {duration_months}M All India"
            else:
                duration_label = f"{duration_months} {'Month' if duration_months == 1 else 'Months'}"
                plan_name = f"{tier} {duration_months}M"

            discount_pct = calculate_discount(mrp, selling_price)

            # Deduplicate by (tier, duration_months, mrp, selling_price)
            key = (tier, duration_months, sub_heading, mrp, selling_price)
            if key in seen:
                continue
            seen.add(key)

            plans.append(
                {
                    "tier": tier,
                    "name": plan_name,
                    "duration_months": duration_months,
                    "duration_label": duration_label,
                    "mrp": mrp,
                    "selling_price": selling_price,
                    "discount_pct": discount_pct,
                    "offer_string": offer_string,
                    "product_id": product_id_code,
                    "widget_name": widget_name,
                    "sub_heading": sub_heading,
                    "offers": _legacy_offers(offer_string),
                }
            )

    logger.info("Extracted %d plan(s) from page", len(plans))
    return plans


def _tier_from_widget(widget_name: str) -> str:
    wl = widget_name.lower()
    if "non plus" in wl or "non-plus" in wl:
        return "ELITE (NON-METRO)" if "pro" not in wl else "PRO (NON-METRO)"
    if "elite plus" in wl or "elite+" in wl:
        return "ELITE PLUS"
    if "pro plus" in wl or "pro+" in wl:
        return "PRO PLUS"
    if "play" in wl:
        return "PLAY"
    if "cpp" in wl or ("pro" in wl and "elite" not in wl):
        return "PRO"
    if "cpe" in wl or "elite" in wl:
        return "ELITE"
    return "ELITE"


def _product_id_from_action(action: object) -> str:
    if not isinstance(action, dict):
        return ""
    url = str(action.get("url") or "")
    match = _RE_ACTION_PRODUCT_ID.search(url)
    if match:
        return match.group(1)
    if url.startswith("/"):
        return url.rstrip("/").rsplit("/", 1)[-1]
    return ""


def _numeric_amount(text: str) -> Optional[float]:
    match = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", text)
    return float(match.group(1).replace(",", "")) if match else None


def _offer_from_text(text: str, *, coupon_code: Optional[str] = None) -> OfferData:
    clean = text.strip()
    upper = clean.upper()
    discount_type = None
    discount_value = None
    category = "Other"

    amount = _numeric_amount(clean)
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", clean)
    if amount is not None and ("OFF" in upper or "DISCOUNT" in upper):
        category = "Instant discount" if "APPLIED" in upper else "Other"
        discount_type = "fixed"
        discount_value = amount
    elif percent:
        category = "Other"
        discount_type = "percentage"
        discount_value = float(percent.group(1))

    if coupon_code:
        category = "Coupon"
    elif "EMI" in upper:
        category = "EMI"
    elif "CASHBACK" in upper:
        category = "Cashback"
    elif "UPI" in upper:
        category = "UPI"
    elif "BANK" in upper or "CARD" in upper:
        category = "Bank discount"

    payment_methods = (
        "EMI" if category == "EMI"
        else "UPI" if category == "UPI"
        else clean if category == "Bank discount"
        else None
    )
    return OfferData(
        title=clean,
        description=clean,
        category=category,
        coupon_code=coupon_code,
        discount_type=discount_type,
        discount_value=discount_value,
        eligible_payment_methods=payment_methods,
        eligibility_conditions=clean if category not in {"Instant discount"} else None,
        is_conditional=None if category == "Other" else category != "Instant discount",
    )


def _legacy_offers(offer_string: str) -> list[dict]:
    if not offer_string:
        return []
    return [_offer_from_text(offer_string).normalized()]


def _parse_structured_plans(blocks: list[str]) -> list[dict]:
    """Parse cardList and offer-card objects from decoded Next.js RSC text."""
    decoder = json.JSONDecoder()
    cards: dict[str, dict] = {}
    detail_by_product: dict[str, list[OfferData]] = {}

    for text in blocks:
        widget_positions = [
            (m.start(), m.group(1)) for m in _RE_WIDGET_NAME.finditer(text)
        ]
        objects: list[tuple[int, dict]] = []
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[index:])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(obj, dict):
                objects.append((index, obj))

        for position, obj in objects:
            card_list = obj.get("cardList")
            if not isinstance(card_list, list):
                continue
            widget_name = str(
                (obj.get("widgetMetric") or {}).get("widgetName") or ""
            )
            if not widget_name:
                for widget_position, name in widget_positions:
                    if widget_position <= position:
                        widget_name = name
            for card in card_list:
                if not isinstance(card, dict):
                    continue
                price = card.get("price") or {}
                action = card.get("action") or {}
                product_id = _product_id_from_action(action)
                if not product_id:
                    continue
                descriptions = [
                    str(item).strip()
                    for item in (card.get("description") or [])
                    if str(item).strip()
                ]
                icon_offers = [
                    _offer_from_text(
                        " ".join(
                            str(icon.get(key)).strip()
                            for key in ("title", "subTitle")
                            if icon.get(key)
                        )
                    )
                    for icon in (obj.get("iconList") or [])
                    if isinstance(icon, dict)
                ]
                cards[product_id] = {
                    "heading": str(card.get("heading") or ""),
                    "sub_heading": str(card.get("subHeading") or ""),
                    "descriptions": descriptions,
                    "mrp": normalize_price(str(price.get("mrp") or "")),
                    "selling_price": normalize_price(str(price.get("listingPrice") or "")),
                    "product_id": product_id,
                    "widget_name": widget_name,
                    "action_url": action.get("url"),
                    "position": position,
                    "icon_offers": icon_offers,
                }

        for _, obj in objects:
            if not isinstance(obj.get("offers"), list) or "offerPrice" not in obj:
                continue
            product_id = _product_id_from_action(obj.get("action"))
            if not product_id:
                continue
            offer_items: list[OfferData] = []
            for item in obj.get("offers", []):
                title = item.get("title") if isinstance(item, dict) else str(item)
                if title:
                    offer_items.append(_offer_from_text(str(title)))
            hero = obj.get("heroOffer") or {}
            hero_title = ((hero.get("heroOfferTitle") or {}).get("text")
                          if isinstance(hero, dict) else None)
            hero_code_text = ((hero.get("heroOfferCode") or {}).get("text")
                              if isinstance(hero, dict) else None)
            code_match = re.search(r"(?:use\s+code\s*:\s*)([A-Z0-9_-]+)", hero_code_text or "", re.I)
            if hero_title:
                offer_items.append(
                    _offer_from_text(
                        str(hero_title),
                        coupon_code=code_match.group(1) if code_match else None,
                    )
                )
            detail_by_product.setdefault(product_id, []).extend(offer_items)

    if not cards:
        return []

    plans: list[dict] = []
    for card in sorted(cards.values(), key=lambda item: item["position"]):
        if card["mrp"] is None or card["selling_price"] is None:
            continue
        duration_months = int(card["heading"]) if card["heading"].isdigit() else 0
        if duration_months <= 0:
            continue
        tier = _tier_from_widget(card["widget_name"])
        all_india = "ALL INDIA" in card["sub_heading"].upper()
        duration_label = (
            f"{duration_months}M All India"
            if all_india
            else f"{duration_months} {'Month' if duration_months == 1 else 'Months'}"
        )
        name = f"{tier} {duration_label if all_india else str(duration_months) + 'M'}"
        offers = detail_by_product.get(card["product_id"], [])
        seen_offer_keys: set[str] = set()
        normalized_offers: list[dict] = []
        for offer in (
            offers
            + [_offer_from_text(text) for text in card["descriptions"]]
            + card.get("icon_offers", [])
        ):
            if not offer.source_url:
                offer.source_url = card.get("action_url")
            key = offer.key()
            if key in seen_offer_keys:
                continue
            seen_offer_keys.add(key)
            normalized_offers.append(offer.normalized())
        plans.append(
            {
                "tier": tier,
                "name": name,
                "duration_months": duration_months,
                "duration_label": duration_label,
                "mrp": card["mrp"],
                "selling_price": card["selling_price"],
                "discount_pct": calculate_discount(card["mrp"], card["selling_price"]),
                "offer_string": " + ".join(card["descriptions"]),
                "product_id": card["product_id"],
                "widget_name": card["widget_name"],
                "sub_heading": card["sub_heading"],
                "offers": normalized_offers,
                "offer_extraction_status": "success",
            }
        )
    return plans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_price(price_str: str) -> Optional[float]:
    """
    Parse a price string like ``'7,344'`` or ``'7344'`` → ``7344.0``.

    Returns ``None`` on parse failure or if the result is 0.
    """
    if not price_str:
        return None
    cleaned = price_str.replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def calculate_discount(mrp: float, selling: float) -> float:
    """
    Return the discount percentage (0–100).

    Returns 0 if *mrp* ≤ *selling* (no discount / premium price).
    """
    if mrp <= 0 or selling >= mrp:
        return 0.0
    return round((mrp - selling) / mrp * 100, 2)
