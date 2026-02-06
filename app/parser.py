import asyncio
import random
import re
import logging

import httpx
from bs4 import BeautifulSoup

from app.models import Company, SearchResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rusprofile.ru"
AJAX_URL = f"{BASE_URL}/ajax.php"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

_last_request_time: float = 0


def _clean_inn(raw: str) -> str:
    return re.sub(r"[!~]", "", raw).strip()


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": BASE_URL,
    }


async def _throttle(delay: float) -> None:
    global _last_request_time
    now = asyncio.get_event_loop().time()
    wait = delay - (now - _last_request_time)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request_time = asyncio.get_event_loop().time()


async def search_ajax(query: str, delay: float = 2.5) -> list[dict]:
    """Search rusprofile via AJAX endpoint. Returns raw result dicts."""
    await _throttle(delay)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(
            AJAX_URL,
            params={"query": query, "action": "search"},
            headers=_get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("ul", []):
        results.append(item)
    for item in data.get("ip", []):
        results.append(item)
    return results


def _parse_ajax_item(item: dict) -> Company:
    """Convert an AJAX result item to a Company model."""
    inn = _clean_inn(item.get("inn", ""))
    inactive = item.get("inactive", 0)
    status = "\u041b\u0438\u043a\u0432\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u0430" if inactive else "\u0414\u0435\u0439\u0441\u0442\u0432\u0443\u044e\u0449\u0430\u044f"
    capital_raw = item.get("authorized_capital")
    capital = None
    if capital_raw:
        try:
            capital = f"{float(capital_raw):,.0f} \u0440\u0443\u0431.".replace(",", " ")
        except (ValueError, TypeError):
            capital = str(capital_raw)

    okpo = item.get("okpo") or None
    if okpo == "":
        okpo = None

    return Company(
        inn=inn,
        ogrn=item.get("ogrn") or item.get("raw_ogrn"),
        name=item.get("raw_name") or item.get("name"),
        status=status,
        address=item.get("address"),
        region=item.get("region"),
        ceo_name=item.get("ceo_name"),
        ceo_title=item.get("ceo_type"),
        okved_code=item.get("main_okved_id"),
        okved_name=item.get("okved_descr"),
        okpo=okpo,
        capital=capital,
        registration_date=item.get("reg_date"),
        url=f"{BASE_URL}{item['url']}" if item.get("url") else None,
    )


async def parse_company_page(url: str, delay: float = 2.5) -> dict:
    """Fetch company HTML page and extract additional fields (KPP, OKATO, etc.)."""
    await _throttle(delay)
    extra: dict = {}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=_get_headers())
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        # KPP from #clip_kpp
        kpp_el = soup.find(id="clip_kpp")
        if kpp_el:
            extra["kpp"] = kpp_el.get_text(strip=True)

        # OKPO from #clip_okpo
        okpo_el = soup.find(id="clip_okpo")
        if okpo_el:
            extra["okpo"] = okpo_el.get_text(strip=True)

        # Full name from itemprop
        legal_el = soup.find(attrs={"itemprop": "legalName"})
        if legal_el:
            extra["full_name"] = legal_el.get_text(strip=True)

        # OKATO from #clip_okato
        okato_el = soup.find(id="clip_okato")
        if okato_el:
            extra["okato"] = okato_el.get_text(strip=True)

        # OKTMO from #clip_oktmo
        oktmo_el = soup.find(id="clip_oktmo")
        if oktmo_el:
            extra["oktmo"] = oktmo_el.get_text(strip=True)

        # OKFS from #clip_okfs
        okfs_el = soup.find(id="clip_okfs")
        if okfs_el:
            extra["okfs"] = okfs_el.get_text(strip=True)

        # OKOGU from #clip_okogu
        okogu_el = soup.find(id="clip_okogu")
        if okogu_el:
            extra["okogu"] = okogu_el.get_text(strip=True)

    except Exception as e:
        logger.warning("Failed to parse company page %s: %s", url, e)

    return extra


async def get_company_by_inn(inn: str, delay: float = 2.5) -> Company | None:
    """Full pipeline: AJAX search by INN + HTML page for extra fields."""
    results = await search_ajax(inn, delay=delay)

    target = None
    for item in results:
        item_inn = _clean_inn(item.get("inn", ""))
        if item_inn == inn:
            target = item
            break

    if not target:
        return None

    company = _parse_ajax_item(target)

    # Fetch HTML page for extra fields
    page_url = f"{BASE_URL}{target['url']}" if target.get("url") else None
    if page_url:
        extra = await parse_company_page(page_url, delay=delay)
        if extra.get("kpp"):
            company.kpp = extra["kpp"]
        if extra.get("full_name"):
            company.full_name = extra["full_name"]
        if extra.get("okpo") and not company.okpo:
            company.okpo = extra["okpo"]
        if extra.get("okato"):
            company.okato = extra["okato"]
        if extra.get("oktmo"):
            company.oktmo = extra["oktmo"]
        if extra.get("okfs"):
            company.okfs = extra["okfs"]
        if extra.get("okogu"):
            company.okogu = extra["okogu"]

    return company


async def get_company_by_ogrn(ogrn: str, delay: float = 2.5) -> Company | None:
    """Search by OGRN -- same AJAX endpoint, match by OGRN."""
    results = await search_ajax(ogrn, delay=delay)

    target = None
    for item in results:
        if item.get("ogrn") == ogrn or item.get("raw_ogrn") == ogrn:
            target = item
            break

    if not target:
        return None

    company = _parse_ajax_item(target)

    page_url = f"{BASE_URL}{target['url']}" if target.get("url") else None
    if page_url:
        extra = await parse_company_page(page_url, delay=delay)
        if extra.get("kpp"):
            company.kpp = extra["kpp"]
        if extra.get("full_name"):
            company.full_name = extra["full_name"]
        if extra.get("okpo") and not company.okpo:
            company.okpo = extra["okpo"]
        if extra.get("okato"):
            company.okato = extra["okato"]
        if extra.get("oktmo"):
            company.oktmo = extra["oktmo"]
        if extra.get("okfs"):
            company.okfs = extra["okfs"]
        if extra.get("okogu"):
            company.okogu = extra["okogu"]

    return company


async def search_companies(query: str, delay: float = 2.5) -> list[SearchResult]:
    """Search by name/query -- returns list of brief results."""
    results = await search_ajax(query, delay=delay)

    out = []
    for item in results:
        inn = _clean_inn(item.get("inn", ""))
        inactive = item.get("inactive", 0)
        out.append(SearchResult(
            inn=inn,
            name=item.get("raw_name") or item.get("name"),
            ogrn=item.get("ogrn"),
            address=item.get("address"),
            ceo_name=item.get("ceo_name"),
            status="\u041b\u0438\u043a\u0432\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u0430" if inactive else "\u0414\u0435\u0439\u0441\u0442\u0432\u0443\u044e\u0449\u0430\u044f",
            url=f"{BASE_URL}{item['url']}" if item.get("url") else None,
        ))
    return out
