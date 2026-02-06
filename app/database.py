import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from app.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, CACHE_TTL_HOURS
from app.models import Company, StatsResponse

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

COLUMNS = [
    "inn", "kpp", "ogrn", "name", "full_name", "status", "address", "region",
    "ceo_name", "ceo_title", "okved_code", "okved_name", "okpo", "oktmo",
    "okato", "okfs", "okogu", "capital", "registration_date", "url",
]


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=1,
        max_size=5,
    )
    logger.info("Database pool created: %s@%s:%s/%s", DB_USER, DB_HOST, DB_PORT, DB_NAME)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_cached(inn: str, force: bool = False) -> Company | None:
    if not _pool or force:
        return None

    row = await _pool.fetchrow(
        "SELECT * FROM organizations WHERE inn = $1", inn
    )
    if not row:
        return None

    updated = row["updated_at"]
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)

    ttl = timedelta(hours=CACHE_TTL_HOURS)
    if datetime.now(timezone.utc) - updated > ttl:
        return None

    return Company(
        **{col: row[col] for col in COLUMNS},
        cached=True,
        cached_at=updated,
    )


async def get_cached_by_ogrn(ogrn: str, force: bool = False) -> Company | None:
    if not _pool or force:
        return None

    row = await _pool.fetchrow(
        "SELECT * FROM organizations WHERE ogrn = $1", ogrn
    )
    if not row:
        return None

    updated = row["updated_at"]
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)

    ttl = timedelta(hours=CACHE_TTL_HOURS)
    if datetime.now(timezone.utc) - updated > ttl:
        return None

    return Company(
        **{col: row[col] for col in COLUMNS},
        cached=True,
        cached_at=updated,
    )


async def save_company(company: Company) -> None:
    if not _pool:
        return

    values = [getattr(company, col) for col in COLUMNS]
    placeholders = ", ".join(f"${i+1}" for i in range(len(COLUMNS)))
    col_names = ", ".join(COLUMNS)
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in COLUMNS if col != "inn")

    query = f"""
        INSERT INTO organizations ({col_names}, created_at, updated_at)
        VALUES ({placeholders}, NOW(), NOW())
        ON CONFLICT (inn) DO UPDATE SET
            {updates},
            updated_at = NOW()
    """
    await _pool.execute(query, *values)


async def get_stats() -> StatsResponse:
    if not _pool:
        return StatsResponse(total_cached=0)

    row = await _pool.fetchrow("""
        SELECT
            COUNT(*) as total,
            MIN(created_at) as oldest,
            MAX(updated_at) as newest
        FROM organizations
    """)
    return StatsResponse(
        total_cached=row["total"],
        oldest_entry=row["oldest"],
        newest_entry=row["newest"],
    )
