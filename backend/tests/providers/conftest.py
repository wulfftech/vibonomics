from __future__ import annotations

from datetime import date

import pytest

from vibonomics.providers import pricing_pages


@pytest.fixture(autouse=True)
def _pricing_page_clock(monkeypatch):
    """The fixture pages carry promos that end on a date; an ended promo is read at its later price, so without a
    fixed day these tests would start failing the day after the first promo ends."""
    monkeypatch.setattr(pricing_pages, "_today", lambda: date(2026, 10, 24))
