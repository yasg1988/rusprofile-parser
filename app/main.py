import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app.config import REQUEST_DELAY
from app.database import init_db, close_db, get_cached, get_cached_by_ogrn, save_company, get_stats
from app.models import Company, SearchResult, StatsResponse
from app.parser import get_company_by_inn, get_company_by_ogrn, search_companies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
    yield
    await close_db()


app = FastAPI(
    title="Rusprofile Parser API",
    description="REST API для получения данных организаций с rusprofile.ru",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def health():
    return {"status": "ok", "service": "rusprofile-parser", "version": "1.0.0"}


@app.get("/company/inn/{inn}", response_model=Company)
async def company_by_inn(inn: str, force: bool = Query(False)):
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(400, "ИНН должен содержать 10 или 12 цифр")

    if not force:
        cached = await get_cached(inn)
        if cached:
            logger.info("Cache hit for INN %s", inn)
            return cached

    logger.info("Parsing rusprofile for INN %s", inn)
    company = await get_company_by_inn(inn, delay=REQUEST_DELAY)
    if not company:
        raise HTTPException(404, f"Организация с ИНН {inn} не найдена")

    try:
        await save_company(company)
    except Exception as e:
        logger.error("Failed to cache company %s: %s", inn, e)

    return company


@app.get("/company/ogrn/{ogrn}", response_model=Company)
async def company_by_ogrn(ogrn: str, force: bool = Query(False)):
    if not ogrn.isdigit() or len(ogrn) not in (13, 15):
        raise HTTPException(400, "ОГРН должен содержать 13 или 15 цифр")

    if not force:
        cached = await get_cached_by_ogrn(ogrn)
        if cached:
            logger.info("Cache hit for OGRN %s", ogrn)
            return cached

    logger.info("Parsing rusprofile for OGRN %s", ogrn)
    company = await get_company_by_ogrn(ogrn, delay=REQUEST_DELAY)
    if not company:
        raise HTTPException(404, f"Организация с ОГРН {ogrn} не найдена")

    try:
        await save_company(company)
    except Exception as e:
        logger.error("Failed to cache company: %s", e)

    return company


@app.get("/search", response_model=list[SearchResult])
async def search(q: str = Query(..., min_length=2)):
    logger.info("Searching for: %s", q)
    results = await search_companies(q, delay=REQUEST_DELAY)
    if not results:
        raise HTTPException(404, f"Ничего не найдено по запросу '{q}'")
    return results


@app.get("/stats", response_model=StatsResponse)
async def stats():
    return await get_stats()
