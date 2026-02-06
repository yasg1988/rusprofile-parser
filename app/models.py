from datetime import datetime

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
