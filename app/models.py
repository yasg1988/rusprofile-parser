from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Company(BaseModel):
    inn: str
    kpp: str | None = None
    ogrn: str | None = None
    name: str | None = None
    full_name: str | None = None
    status: str | None = None
    address: str | None = None
    region: str | None = None
    ceo_name: str | None = None
    ceo_title: str | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    okpo: str | None = None
    oktmo: str | None = None
    okato: str | None = None
    okfs: str | None = None
    okogu: str | None = None
    capital: str | None = None
    registration_date: str | None = None
    url: str | None = None

    # Group 1: Main card extended
    okopf_code: str | None = None
    okopf_name: str | None = None
    ogrn_date: str | None = None
    ceo_inn: str | None = None
    ceo_start_date: str | None = None
    ceo_other_companies: int | None = None
    msp_status: str | None = None
    msp_date: str | None = None
    tax_authority: str | None = None
    tax_authority_date: str | None = None
    address_unreliable: bool | None = None

    # Group 2: Finances
    revenue: str | None = None
    revenue_year: int | None = None
    revenue_change: str | None = None
    profit: str | None = None
    profit_change: str | None = None
    financial_stability: str | None = None
    solvency: str | None = None
    efficiency: str | None = None

    # Group 3: Founders (JSONB)
    founders: list[dict[str, Any]] | None = None

    # Group 4: Reliability
    reliability_rating: str | None = None
    reliability_positive: int | None = None
    reliability_warning: int | None = None
    reliability_negative: int | None = None

    # Group 5: Enforcement
    enforcement_count: int | None = None
    enforcement_sum: str | None = None

    # Group 6: Taxes
    taxes_sum: str | None = None
    taxes_year: int | None = None
    contributions_sum: str | None = None

    # Group 7: Sections overview (JSONB)
    sections: dict[str, Any] | None = None

    cached: bool = False
    cached_at: datetime | None = None


class SearchResult(BaseModel):
    inn: str
    name: str | None = None
    ogrn: str | None = None
    address: str | None = None
    ceo_name: str | None = None
    status: str | None = None
    url: str | None = None


class StatsResponse(BaseModel):
    total_cached: int
    oldest_entry: datetime | None = None
    newest_entry: datetime | None = None
