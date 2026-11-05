"""The shared pricing-page helpers: price cells, promo dates and matching a page row to an offer."""

from __future__ import annotations

from datetime import date

import pytest

from vibonomics.providers import pricing_pages
from vibonomics.providers.base import ModelOffer
from vibonomics.providers.pricing_pages import PagePrice, apply_page_prices, parse_price_cell

# -- a struck-through list price (PR-4) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "current", "listed"),
    [
        ("~~$15 / MTok~~ $7.50 / MTok", 7.5, 15.0),  # Anthropic writes every price as "$X / MTok"
        ("~~$4.00~~ Free", 0.0, 4.0),
        ("~~$4.00~~ free of charge", 0.0, 4.0),
        ("$7.50 / MTok ~~$15 / MTok~~", 7.5, 15.0),  # the strike need not come first
        ("~~$4.00~~ $2.00", 2.0, 4.0),
        ("~~$4.00~~$2.00", 2.0, 4.0),
        ("~~$4.00~~ $2.00 (50% off)", 2.0, 4.0),
        ("~~n/a~~ $2.00", 2.0, None),
    ],
)
def test_a_strikethrough_promo_is_read_wherever_it_sits(cell, current, listed):
    got_current, got_listed, _until, _note = parse_price_cell(cell)
    assert (got_current, got_listed) == (current, listed)


def test_a_strikethrough_promo_carries_a_discount_note():
    assert parse_price_cell("~~$15 / MTok~~ $7.50 / MTok")[3] == "50% off"
    assert parse_price_cell("~~$4.00~~ $2.00 (40% off)")[3] == "40% off"
    assert parse_price_cell("~~$4.00~~ Free")[3] == "100% off"


@pytest.mark.parametrize(
    "cell",
    [
        "~~$4.00 $2.00",  # never closed
        "$4.00~~ $2.00",  # never opened
        "~~$4.00~~ $2.00~~",  # a stray third
        "~~$4.00~~",  # struck, and nothing replaces it
        "~~$4.00~~ not available",
    ],
)
def test_an_unpaired_or_priceless_strike_gives_no_price_so_the_offer_keeps_its_own(cell):
    # Returning the struck list price as the live one would show no discount, and a stale price as current.
    assert parse_price_cell(cell) == (None, None, None, None)


def test_a_plain_cell_is_unchanged():
    assert parse_price_cell("$0.75 / MTok")[:2] == (0.75, None)
    assert parse_price_cell("Free")[:2] == (0.0, None)
    assert parse_price_cell("-")[:2] == (None, None)
    assert parse_price_cell(None) == (None, None, None, None)


# -- a promo with an end date (the "Minor" item under PR-16) ------------------------------------

PROMO = "$2.00 through November 30, 2026. $4.00 starting December 1, 2026."


def test_a_promo_still_running_keeps_its_price():
    current, listed, until, note = parse_price_cell(PROMO)
    assert (current, listed, until) == (2.0, 4.0, "2026-11-30")
    assert note == "50% off until 2026-11-30"


def test_the_last_day_of_a_promo_still_counts(monkeypatch):
    monkeypatch.setattr(pricing_pages, "_today", lambda: date(2026, 11, 30))
    assert parse_price_cell(PROMO)[0] == 2.0


def test_an_expired_promo_gives_the_price_that_replaced_it(monkeypatch):
    monkeypatch.setattr(pricing_pages, "_today", lambda: date(2026, 12, 1))
    assert parse_price_cell(PROMO) == (4.0, None, None, None)


def test_a_promo_whose_end_date_cannot_be_read_is_left_as_it_is():
    cell = "$2.00 through Smarch 31, 2026. $4.00 starting April 1, 2026."
    assert parse_price_cell(cell)[:2] == (2.0, 4.0)


# -- matching a page row to an offer (PR-6) -----------------------------------------------------


def offer(model_id: str, name: str | None = None) -> ModelOffer:
    return ModelOffer(model_id=model_id, name=name or model_id)


def row(name: str, model_id: str | None = None, price: float = 2.0) -> PagePrice:
    return PagePrice(name=name, model_id=model_id, input=price, output=price * 3)


def test_a_new_dated_release_does_not_reprice_the_older_snapshot():
    old = offer("mistral-large-2411")
    assert apply_page_prices([old], [row("mistral-large-2512", "mistral-large-2512")], "page") == 0
    assert old.price.input is None


def test_a_new_dated_release_found_by_name_does_not_reprice_the_older_snapshot():
    old = offer("mistral-large-latest", "Mistral Large 2411")
    assert apply_page_prices([old], [row("Mistral Large 2512")], "page") == 0
    assert old.price.input is None


def test_each_dated_snapshot_gets_its_own_price():
    old, new = offer("mistral-large-2411"), offer("mistral-large-2512")
    rows = [row("mistral-large-2512", "mistral-large-2512", 2.0), row("mistral-large-2411", "mistral-large-2411", 3.0)]
    assert apply_page_prices([old, new], rows, "page") == 2
    assert (old.price.input, new.price.input) == (3.0, 2.0)


def test_a_dated_row_still_finds_the_offer_with_no_date_and_the_other_way_round():
    undated = offer("gpt-4o")
    assert apply_page_prices([undated], [row("gpt-4o-2024-05-13", "gpt-4o-2024-05-13")], "page") == 1
    assert undated.price.input == 2.0
    dated = offer("claude-opus-5-5-20260921")
    assert apply_page_prices([dated], [row("claude-opus-5-5", "claude-opus-5-5", 4.0)], "page") == 1
    assert dated.price.input == 4.0


def test_the_same_snapshot_written_another_way_still_matches():
    dated = offer("claude-opus-5-5-20260921")
    assert apply_page_prices([dated], [row("claude-opus-5-5-2026-09-21", "claude-opus-5-5-2026-09-21")], "page") == 1
