import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from app.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, CACHE_TTL_HOURS
from app.models import Company, StatsResponse

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# All columns in the organizations table (order matters for save)
COLUMNS = [
    "inn", "kpp", "ogrn", "name", "full_name", "status", "address", "region",
    "ceo_name", "ceo_title", "okved_code", "okved_name", "okpo", "oktmo",
    "okato", "okfs", "okogu", "capital", "registration_date", "url",
    # New fields v2
    "okopf_code", "okopf_name", "ogrn_date",
    "ceo_inn", "ceo_start_date", "ceo_other_companies",
    "msp_status", "msp_date",
    "tax_authority", "tax_authority_date", "address_unreliable",
    "revenue", "revenue_year", "revenue_change",
    "profit", "profit_change",
    "financial_stability", "solvency", "efficiency",
    "founders",
    "reliability_rating", "reliability_positive", "reliability_warning", "reliability_negative",
    "enforcement_count", "enforcement_sum",
    "taxes_sum", "taxes_year", "contributions_sum",
    "sections",
]

# Columns stored as JSONB in PostgreSQL
JSONB_COLUMNS = {"founders", "sections"}

# Column type mapping for auto-migration
COLUMN_TYPES = {
    "okopf_code": "TEXT",
    "okopf_name": "TEXT",
    "ogrn_date": "TEXT",
    "ceo_inn": "TEXT",
    "ceo_start_date": "TEXT",
    "ceo_other_companies": "INTEGER",
    "msp_status": "TEXT",
    "msp_date": "TEXT",
    "tax_authority": "TEXT",
    "tax_authority_date": "TEXT",
    "address_unreliable": "BOOLEAN",
    "revenue": "TEXT",
    "revenue_year": "INTEGER",
    "revenue_change": "TEXT",
    "profit": "TEXT",
    "profit_change": "TEXT",
    "financial_stability": "TEXT",
    "solvency": "TEXT",
    "efficiency": "TEXT",
    "founders": "JSONB",
    "reliability_rating": "TEXT",
    "reliability_positive": "INTEGER",
    "reliability_warning": "INTEGER",
    "reliability_negative": "INTEGER",
    "enforcement_count": "INTEGER",
    "enforcement_sum": "TEXT",
    "taxes_sum": "TEXT",
    "taxes_year": "INTEGER",
    "contributions_sum": "TEXT",
    "sections": "JSONB",
}


async def _ensure_pool() -> asyncpg.Pool | None:
    """Lazy pool initialization with retry on each call."""
    global _pool
    if _pool:
        return _pool
    try:
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
    except Exception as e:
        logger.warning("Database unavailable: %s", e)
        _pool = None
    return _pool


async def _auto_migrate(pool: asyncpg.Pool) -> None:
    """Add new columns to organizations table if they don't exist."""
    for col_name, col_type in COLUMN_TYPES.items():
        try:
            await pool.execute(
                f"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
        except Exception as e:
            logger.warning("Migration failed for column %s: %s", col_name, e)
    logger.info("Auto-migration complete: %d new columns checked", len(COLUMN_TYPES))


async def init_db() -> None:
    pool = await _ensure_pool()
    if pool:
        await _auto_migrate(pool)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _row_to_company(row, cached: bool = True) -> Company:
    """Convert a database row to a Company model, handling JSONB deserialization."""
    data = {}
    for col in COLUMNS:
        val = row.get(col)
        if col in JSONB_COLUMNS and isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
        data[col] = val

    updated = row["updated_at"]
    if updated and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)

    return Company(**data, cached=cached, cached_at=updated)


async def get_cached(inn: str, force: bool = False) -> Company | None:
    pool = await _ensure_pool()
    if not pool or force:
        return None

    row = await pool.fetchrow(
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

    return _row_to_company(row)


async def get_cached_by_ogrn(ogrn: str, force: bool = False) -> Company | None:
    pool = await _ensure_pool()
    if not pool or force:
        return None

    row = await pool.fetchrow(
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

    return _row_to_company(row)


async def save_company(company: Company) -> None:
    pool = await _ensure_pool()
    if not pool:
        return

    values = []
    for col in COLUMNS:
        val = getattr(company, col)
        # Serialize JSONB columns
        if col in JSONB_COLUMNS and val is not None:
            val = json.dumps(val, ensure_ascii=False)
        values.append(val)

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
    await pool.execute(query, *values)


async def get_stats() -> StatsResponse:
    pool = await _ensure_pool()
    if not pool:
        return StatsResponse(total_cached=0)

    row = await pool.fetchrow("""
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
