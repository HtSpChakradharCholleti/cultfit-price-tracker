"""
Unit tests for price scraper, parsing, normalization, and error handling.
Uses mocked HTML rather than live website requests.
"""

import json
import pytest
from unittest.mock import patch

from app.scraper.extraction import (
    calculate_discount,
    normalize_price,
    parse_plans,
)
from app.scraper.cultfit import CultFitScraper, ScrapingResult


def test_normalize_price_valid():
    """Verify normal numeric strings and comma-formatted prices."""
    assert normalize_price("7,344") == 7344.0
    assert normalize_price("19450") == 19450.0
    assert normalize_price("1,00,000") == 100000.0
    assert normalize_price("  12590  ") == 12590.0


def test_normalize_price_invalid_and_zero():
    """Verify invalid, missing, and zero prices return None, never 0."""
    assert normalize_price("") is None
    assert normalize_price(None) is None
    assert normalize_price("abc") is None
    assert normalize_price("₹") is None
    assert normalize_price("0") is None
    assert normalize_price("0.0") is None
    assert normalize_price("-500") is None


def test_calculate_discount():
    """Verify discount percentages and edge cases."""
    assert calculate_discount(19450.0, 16950.0) == 12.85
    assert calculate_discount(10000.0, 5000.0) == 50.0
    # No discount
    assert calculate_discount(10000.0, 10000.0) == 0.0
    # Premium / selling > mrp
    assert calculate_discount(10000.0, 12000.0) == 0.0
    # Invalid mrp
    assert calculate_discount(0.0, 5000.0) == 0.0
    assert calculate_discount(-100.0, 5000.0) == 0.0


def _build_mock_nextjs_html(inner_payload: str) -> str:
    """Helper to wrap JSON payload into Next.js self.__next_f.push structure."""
    encoded = json.dumps(inner_payload)
    return f"""
    <!DOCTYPE html><html><head></head><body>
    <script>self.__next_f.push([1,{encoded}])</script>
    </body></html>
    """


def test_parse_plans_extracted_successfully():
    """Verify full extraction from mocked Next.js RSC payload."""
    payload = (
        '{"widgetName":"Elite - S1 - 12M Sku"}'
        '{"heading":"12","subHeading":"MONTHS","description":["Free access"],'
        '"price":{"mrp":"19,450","listingPrice":"16,950","showPriceCut":true,"currency":"₹"},'
        '"offerString":"Extra ₹2,500 OFF applied","productId":"CULTPACK_WY"}'
        '{"widgetName":"Elite - S1 - 1M Sku"}'
        '{"heading":"1","subHeading":"MONTH","description":["15% off activewear"],'
        '"price":{"mrp":"7,344","listingPrice":"7,344","showPriceCut":false,"currency":"₹"},'
        '"offerString":"","productId":"CULTPACK_1M"}'
    )
    html = _build_mock_nextjs_html(payload)
    plans = parse_plans(html)

    assert len(plans) == 2

    p12 = next(p for p in plans if p["duration_months"] == 12)
    assert p12["tier"] == "ELITE"
    assert p12["mrp"] == 19450.0
    assert p12["selling_price"] == 16950.0
    assert p12["discount_pct"] == 12.85
    assert p12["offer_string"] == "Extra ₹2,500 OFF applied"
    assert p12["product_id"] == "CULTPACK_WY"

    p1 = next(p for p in plans if p["duration_months"] == 1)
    assert p1["tier"] == "ELITE"
    assert p1["mrp"] == 7344.0
    assert p1["selling_price"] == 7344.0
    assert p1["discount_pct"] == 0.0


def test_parse_plans_deduplication():
    """Verify duplicate plan blocks in HTML are cleanly deduplicated."""
    payload = (
        '{"widgetName":"Elite - S1 - 12M Sku"}'
        '{"heading":"12","subHeading":"MONTHS",'
        '"price":{"mrp":"19,450","listingPrice":"16,950","showPriceCut":true,"currency":"₹"}}'
        '{"widgetName":"Elite - S1 - 12M Sku"}'
        '{"heading":"12","subHeading":"MONTHS",'
        '"price":{"mrp":"19,450","listingPrice":"16,950","showPriceCut":true,"currency":"₹"}}'
    )
    html = _build_mock_nextjs_html(payload)
    plans = parse_plans(html)
    assert len(plans) == 1


def test_parse_plans_tier_detection():
    """Verify tier detection recognizes ELITE, PRO, and PLAY widgets."""
    payload = (
        '{"widgetName":"Elite - S1 - 6M Sku"}'
        '{"heading":"6","subHeading":"MONTHS","price":{"mrp":"15,850","listingPrice":"13,850"}}'
        '{"widgetName":"PRO - S20 - 6M Sku"}'
        '{"heading":"6","subHeading":"MONTHS","price":{"mrp":"10,790","listingPrice":"10,190"}}'
        '{"widgetName":"PLAY - S1 - 6M SKU"}'
        '{"heading":"6","subHeading":"MONTHS","price":{"mrp":"15,785","listingPrice":"13,990"}}'
    )
    html = _build_mock_nextjs_html(payload)
    plans = parse_plans(html)
    tiers = {p["tier"] for p in plans}
    assert tiers == {"ELITE", "PRO", "PLAY"}


def test_scraper_cloudflare_guard():
    """Verify Cloudflare detection raises error and is captured in ScrapingResult."""
    scraper = CultFitScraper()
    with patch("app.scraper.cultfit.fetch_page", side_effect=RuntimeError("Cloudflare detected")):
        res = scraper.scrape()
        assert res.success is False
        assert "Cloudflare detected" in res.error
        assert len(res.plans) == 0


def test_scraper_http_error_handling():
    """Verify HTTP errors are handled gracefully without crashing."""
    scraper = CultFitScraper()
    with patch("app.scraper.cultfit.fetch_page", side_effect=Exception("Connection refused")):
        res = scraper.scrape()
        assert res.success is False
        assert "Connection refused" in res.error
        assert len(res.plans) == 0

def test_parse_plans_non_metro_isolation():
    """Verify non-plus / non-metro cards are tagged separately and do not collide with metro plans."""
    payload = (
        '{"widgetName":"BASE - Desktop - CPE Reason To Buy Widget"}'
        '{"heading":"12","subHeading":"MONTHS","price":{"mrp":"19,450","listingPrice":"16,950"}}'
        '{"widgetName":"Elite LP - Pack Browse Widget - Non Plus Cities"}'
        '{"heading":"12","subHeading":"MONTHS","price":{"mrp":"27,990","listingPrice":"25,490"}}'
    )
    html = _build_mock_nextjs_html(payload)
    plans = parse_plans(html)
    assert len(plans) == 2

    metro = next(p for p in plans if p["tier"] == "ELITE")
    assert metro["selling_price"] == 16950.0
    assert metro["name"] == "ELITE 12M"

    non_metro = next(p for p in plans if "NON-METRO" in p["tier"])
    assert non_metro["selling_price"] == 25490.0
    assert "NON-METRO" in non_metro["name"]


def test_parse_structured_plan_offers_and_coupon():
    payload = (
        '{"widgetMetric":{"widgetName":"BASE - Desktop - CPE Reason To Buy Widget"},'
        '"iconList":[{"title":"No-Cost","subTitle":"EMI available"}],'
        '"cardList":[{"heading":"12","subHeading":"MONTHS",'
        '"description":["Extra ₹2,500 OFF applied","15% OFF* on cult activewear"],'
        '"price":{"mrp":"19,450","listingPrice":"16,950"},'
        '"action":{"url":"curefit://cultpack?productId=CULTPACK_X"}}]}'
        '{"productType":"CULT_UNLIMITED","costPrice":"19450",'
        '"offerPrice":"16950","offers":[{"title":"Extra ₹2,500 OFF applied"}],'
        '"heroOffer":{"heroOfferTitle":{"text":"₹1,200 Amazon Pay Gift Card"},'
        '"heroOfferCode":{"text":"Use code: ELITE45D1200AMZ"}},'
        '"action":{"url":"curefit://cultpack?productId=CULTPACK_X"}}'
    )
    plans = parse_plans(_build_mock_nextjs_html(payload))
    assert len(plans) == 1
    plan = plans[0]
    assert plan["product_id"] == "CULTPACK_X"
    assert plan["tier"] == "ELITE"
    assert any(o["category"] == "Instant discount" for o in plan["offers"])
    assert any(o["coupon_code"] == "ELITE45D1200AMZ" for o in plan["offers"])
    assert any(o["category"] == "EMI" for o in plan["offers"])
