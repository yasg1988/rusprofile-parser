import asyncio
import json
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
    status = "Ликвидирована" if inactive else "Действующая"
    capital_raw = item.get("authorized_capital")
    capital = None
    if capital_raw:
        try:
            capital = f"{float(capital_raw):,.0f} руб.".replace(",", " ")
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


def _get_next_dds(dt_el) -> list:
    """Get all consecutive <dd> siblings after a <dt> element."""
    dds = []
    sib = dt_el.next_sibling
    while sib:
        if hasattr(sib, "name"):
            if sib.name == "dd":
                dds.append(sib)
            elif sib.name == "dt":
                break
        sib = sib.next_sibling
    return dds


def _find_dt_by_text(soup, text: str):
    """Find a <dt> element containing the given text."""
    for dt in soup.find_all("dt"):
        if text in dt.get_text():
            return dt
    return None


def _parse_basic_fields(soup) -> dict:
    """Extract basic fields from clip elements and dt/dd pairs."""
    extra = {}

    # Clip-based fields
    clip_map = {
        "clip_kpp": "kpp",
        "clip_okpo": "okpo",
        "clip_okato": "okato",
        "clip_oktmo": "oktmo",
        "clip_okfs": "okfs",
        "clip_okogu": "okogu",
    }
    for clip_id, field in clip_map.items():
        el = soup.find(id=clip_id)
        if el:
            extra[field] = el.get_text(strip=True)

    # Full name
    legal_el = soup.find(attrs={"itemprop": "legalName"})
    if legal_el:
        extra["full_name"] = legal_el.get_text(strip=True)
    if not extra.get("full_name"):
        clip_name = soup.find(id="clip_name-long")
        if clip_name:
            extra["full_name"] = clip_name.get_text(strip=True)

    # ОКОПФ code and name
    okopf_el = soup.find(id="clip_okopf")
    if okopf_el:
        extra["okopf_code"] = okopf_el.get_text(strip=True)
        # Name is in the next <dd> with class="chief-title" span
        parent_dd = okopf_el.find_parent("dd")
        if parent_dd:
            next_dd = parent_dd.find_next_sibling("dd")
            if next_dd:
                chief = next_dd.find("span", class_="chief-title")
                if chief:
                    extra["okopf_name"] = chief.get_text(strip=True)

    # OGRN date from second <dd> after <dt>ОГРН</dt>
    ogrn_dt = _find_dt_by_text(soup, "ОГРН")
    if ogrn_dt:
        dds = _get_next_dds(ogrn_dt)
        for dd in dds:
            dd_text = dd.get_text(strip=True)
            m = re.search(r"от\s+(.+)", dd_text)
            if m:
                extra["ogrn_date"] = m.group(1).strip()
                break

    return extra


def _parse_ceo(soup) -> dict:
    """Extract CEO details from the company-row block."""
    extra = {}

    # Find CEO row by company-info__title or company-row
    ceo_row = None
    for row in soup.find_all(class_="company-row"):
        title_el = row.find(class_="company-info__title")
        if title_el and "уководител" in title_el.get_text():
            ceo_row = row
            break

    if not ceo_row:
        return extra

    # CEO INN from person link (12-digit suffix in slug)
    person_link = ceo_row.find("a", href=re.compile(r"/person/"))
    if person_link:
        href = person_link.get("href", "")
        slug = href.rstrip("/").split("/")[-1] if "/" in href else ""
        inn_match = re.search(r"(\d{12})$", slug)
        if inn_match:
            extra["ceo_inn"] = inn_match.group(1)

    # CEO start date ("с 22 января 2008 г.")
    for span in ceo_row.find_all("span", class_="chief-title"):
        span_text = span.get_text(strip=True)
        date_match = re.search(r"с\s+(\d+\s+\w+\s+\d{4}\s*г\.?)", span_text)
        if date_match:
            extra["ceo_start_date"] = date_match.group(1).strip()
            break

    # CEO other companies ("еще N организаций" or "Руководитель еще N организаций")
    row_text = ceo_row.get_text()
    other_match = re.search(r"еще\s+(\d+)\s+организаци", row_text)
    if other_match:
        extra["ceo_other_companies"] = int(other_match.group(1))

    return extra


def _parse_msp(soup) -> dict:
    """Extract MSP (small/medium enterprise registry) status."""
    extra = {}

    # Try company-info structure
    for row in soup.find_all(class_="company-row"):
        title_el = row.find(class_="company-info__title")
        if title_el and "Реестр МСП" in title_el.get_text():
            text_el = row.find(class_="company-info__text")
            if text_el:
                msp_text = text_el.get_text(strip=True)
                if msp_text and msp_text != "не входит":
                    extra["msp_status"] = msp_text
                    # Try to extract date
                    date_match = re.search(r"с?\s*(\d[\d.]+\d{4}|\d+\s+\w+\s+\d{4})", msp_text)
                    if date_match:
                        extra["msp_date"] = date_match.group(1)
            return extra

    # Try dt/dd pattern
    msp_dt = _find_dt_by_text(soup, "Реестр МСП")
    if msp_dt:
        dd = msp_dt.find_next_sibling("dd")
        if dd:
            msp_text = dd.get_text(strip=True)
            if msp_text and msp_text != "не входит":
                extra["msp_status"] = msp_text

    return extra


def _parse_tax_authority(soup) -> dict:
    """Extract tax authority name and date."""
    extra = {}

    # Try dt/dd pattern
    tax_dt = _find_dt_by_text(soup, "Налоговый орган")
    if tax_dt:
        dd = tax_dt.find_next_sibling("dd")
        if dd:
            tax_text = dd.get_text(strip=True)
            # "Название ФНС с 20 июля 2018 г."
            date_match = re.search(r"(.+?)\s+с\s+(\d+\s+\w+\s+\d{4}\s*г\.?)\s*$", tax_text)
            if date_match:
                extra["tax_authority"] = date_match.group(1).strip()
                extra["tax_authority_date"] = date_match.group(2).strip()
            else:
                extra["tax_authority"] = tax_text
        return extra

    # Try company-info structure (separate spans)
    for el in soup.find_all(string=re.compile(r"Налоговый орган")):
        parent = el.parent
        if parent:
            container = parent.parent
            if container:
                texts = [s.strip() for s in container.stripped_strings]
                # Find the text after "Налоговый орган"
                for i, t in enumerate(texts):
                    if "Налоговый орган" in t and i + 1 < len(texts):
                        name = texts[i + 1]
                        date = texts[i + 2] if i + 2 < len(texts) else None
                        extra["tax_authority"] = name
                        if date:
                            date_match = re.search(r"с\s+(.+)", date)
                            if date_match:
                                extra["tax_authority_date"] = date_match.group(1).strip()
                        break
        break

    return extra


def _parse_finances(soup) -> dict:
    """Extract financial data from the finance tile."""
    extra = {}

    finance_tile = soup.find(class_="finance-tile")
    if not finance_tile:
        return extra

    tile_text = finance_tile.get_text()
    if "отсутствуют" in tile_text:
        return extra

    # Revenue year from context
    year_match = re.search(r"за\s+(\d{4})\s*(?:год|г\.?)", tile_text)
    if year_match:
        extra["revenue_year"] = int(year_match.group(1))

    for dt in finance_tile.find_all("dt"):
        dt_text = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        dd_text = dd.get_text(strip=True)

        if "Выручка" in dt_text:
            amount_match = re.match(r"(.+?руб\.?)", dd_text)
            if amount_match:
                extra["revenue"] = amount_match.group(1).strip()
            change_match = re.search(r"([↑↓±]?[+\-]?\d+[\s,.]?\d*\s*%)", dd_text)
            if change_match:
                extra["revenue_change"] = change_match.group(1).strip()

        elif "Прибыль" in dt_text:
            amount_match = re.match(r"(.+?руб\.?)", dd_text)
            if amount_match:
                extra["profit"] = amount_match.group(1).strip()
            change_match = re.search(r"([↑↓±]?[+\-]?\d+[\s,.]?\d*\s*%)", dd_text)
            if change_match:
                extra["profit_change"] = change_match.group(1).strip()

    # Financial stability, solvency, efficiency (may appear as summary text)
    stability_match = re.search(r"(?:стабильность|устойчивость)[:\s]*(\w+)", tile_text, re.IGNORECASE)
    if stability_match:
        extra["financial_stability"] = stability_match.group(1).upper()
    solvency_match = re.search(r"(?:платёжеспособность|платежеспособность)[:\s]*(.+?)(?:\.|$)", tile_text, re.IGNORECASE)
    if solvency_match:
        extra["solvency"] = solvency_match.group(1).strip()
    efficiency_match = re.search(r"(?:эффективность)[:\s]*(\w+)", tile_text, re.IGNORECASE)
    if efficiency_match:
        extra["efficiency"] = efficiency_match.group(1).upper()

    return extra


def _parse_founders(soup) -> dict:
    """Extract founders from the founders tile."""
    extra = {}

    founders_tile = soup.find(class_="founders-tile")
    if not founders_tile:
        return extra

    founder_items = founders_tile.find_all(class_="founder-item")
    if not founder_items:
        return extra

    founders = []
    for item in founder_items:
        founder = {}

        # Name and type
        title_el = item.find(class_="founder-item__title")
        if title_el:
            link = title_el.find("a")
            if link:
                founder["name"] = link.get_text(strip=True)
                href = link.get("href", "")
                if "/person/" in href:
                    founder["type"] = "physical"
                    # Try to extract INN from person URL
                    slug = href.rstrip("/").split("/")[-1]
                    inn_m = re.search(r"(\d{12})$", slug)
                    if inn_m:
                        founder["inn"] = inn_m.group(1)
                elif "/id/" in href:
                    founder["type"] = "legal"
            else:
                founder["name"] = title_el.get_text(strip=True)

        # INN from dt/dd
        inn_dt = item.find("dt", string=re.compile(r"ИНН"))
        if inn_dt:
            inn_dd = inn_dt.find_next_sibling("dd")
            if inn_dd:
                inn_span = inn_dd.find("span", class_="inn")
                inn_text = inn_span.get_text(strip=True) if inn_span else inn_dd.get_text(strip=True)
                if inn_text and re.match(r"^\d{10,12}$", inn_text):
                    founder["inn"] = inn_text

        # Share
        share_dt = item.find("dt", string=re.compile(r"Доля"))
        if share_dt:
            share_dd = share_dt.find_next_sibling("dd")
            if share_dd:
                founder["share"] = share_dd.get_text(strip=True)

        if founder.get("name"):
            founders.append(founder)

    if founders:
        extra["founders"] = founders

    return extra


def _parse_enforcement(soup) -> dict:
    """Extract enforcement proceedings from FSSP tile."""
    extra = {}

    fssp_tile = soup.find(class_="fssp-tile")
    if not fssp_tile:
        return extra

    tile_text = fssp_tile.get_text()
    if "не найдена" in tile_text or "отсутствуют" in tile_text:
        return extra

    # Count - look for "Производств" followed by number
    prod_text = re.search(r"Производств\D*(\d+)", tile_text)
    if prod_text:
        extra["enforcement_count"] = int(prod_text.group(1))

    # Sum - "На сумму X руб."
    sum_match = re.search(r"(?:[Нн]а сумму)\s+(.+?руб\.?)", tile_text)
    if sum_match:
        extra["enforcement_sum"] = sum_match.group(1).strip()

    return extra


def _parse_taxes(soup) -> dict:
    """Extract tax data from the taxes tile."""
    extra = {}

    taxes_tile = soup.find(class_="taxes-tile")
    if not taxes_tile:
        return extra

    tile_text = taxes_tile.get_text()
    if "не найдена" in tile_text or "отсутствуют" in tile_text:
        return extra

    for dt in taxes_tile.find_all("dt"):
        dt_text = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        dd_text = dd.get_text(strip=True)

        if "Налог" in dt_text and dd_text:
            extra["taxes_sum"] = dd_text
        elif "Взнос" in dt_text and dd_text:
            extra["contributions_sum"] = dd_text

    # Year
    year_match = re.search(r"за\s+(\d{4})", tile_text)
    if year_match:
        extra["taxes_year"] = int(year_match.group(1))

    return extra


def _parse_reliability(soup) -> dict:
    """Extract reliability rating from RPF.store JavaScript."""
    extra = {}

    for script in soup.find_all("script"):
        script_text = script.string or ""
        if "check_counterparty" not in script_text:
            continue

        rel_match = re.search(r"""reliability['"]\s*:\s*['"](\w+)['"]""", script_text)
        if rel_match:
            raw = rel_match.group(1)
            mapping = {
                "positive": "ВЫСОКАЯ",
                "normal": "СРЕДНЯЯ",
                "negative": "НИЗКАЯ",
            }
            extra["reliability_rating"] = mapping.get(raw, raw)
        break

    return extra


def _parse_sections(soup) -> dict:
    """Build a sections overview dict from h2 tile headings."""
    section_tiles = {
        "arbitr-tile": "arbitration",
        "trademarks-tile": "trademarks",
        "gz-tile": "gov_contracts",
        "inspections-tile": "inspections",
        "branches-tile": "branches",
        "licenses-tile": "licenses",
        "leasing-tile": "leasing",
        "pledge-tile": "pledges",
        "facts-tile": "fedresurs",
        "sou-tile": "courts",
    }

    no_data_markers = ["не найден", "отсутствуют", "не обнаружен"]

    sections = {}
    for css_class, key in section_tiles.items():
        tile = soup.find(class_=lambda c: c and css_class in c)
        if not tile:
            continue

        text = tile.get_text().lower()
        has_data = not any(marker in text for marker in no_data_markers)
        entry = {"exists": has_data}

        if has_data:
            # Try to extract a count from link text
            for link in tile.find_all("a"):
                link_text = link.get_text(strip=True)
                if link_text.isdigit():
                    entry["count"] = int(link_text)
                    break

            # Try to extract sum for financial sections
            sum_match = re.search(r"на сумму\s+(.+?руб\.?)", tile.get_text(), re.IGNORECASE)
            if sum_match:
                entry["sum"] = sum_match.group(1).strip()

        sections[key] = entry

    return {"sections": sections} if sections else {}


def _parse_address_unreliable(soup) -> dict:
    """Check if address is flagged as unreliable."""
    page_text = soup.get_text().lower()
    if "недостоверн" in page_text and "адрес" in page_text:
        return {"address_unreliable": True}
    return {}


async def parse_company_page(url: str, delay: float = 2.5) -> dict:
    """Fetch company HTML page and extract all available fields."""
    await _throttle(delay)
    extra: dict = {}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=_get_headers())
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        # Parse all sections
        parsers = [
            _parse_basic_fields,
            _parse_ceo,
            _parse_msp,
            _parse_tax_authority,
            _parse_finances,
            _parse_founders,
            _parse_enforcement,
            _parse_taxes,
            _parse_reliability,
            _parse_sections,
            _parse_address_unreliable,
        ]
        for parser_fn in parsers:
            try:
                extra.update(parser_fn(soup))
            except Exception as e:
                logger.warning("Parser %s failed for %s: %s", parser_fn.__name__, url, e)

    except Exception as e:
        logger.warning("Failed to parse company page %s: %s", url, e)

    return extra


def _apply_extra(company: Company, extra: dict) -> None:
    """Apply all extra parsed fields to a Company instance."""
    for key, value in extra.items():
        if value is not None and hasattr(company, key):
            # Don't overwrite non-None values with None, and don't overwrite
            # existing non-empty values unless the new value is also non-empty
            current = getattr(company, key)
            if current is None or (value and key not in ("okpo",)):
                setattr(company, key, value)


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
        _apply_extra(company, extra)

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
        _apply_extra(company, extra)

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
            status="Ликвидирована" if inactive else "Действующая",
            url=f"{BASE_URL}{item['url']}" if item.get("url") else None,
        ))
    return out
