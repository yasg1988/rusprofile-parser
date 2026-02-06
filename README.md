# rusprofile-parser

REST API сервис для получения данных организаций с rusprofile.ru.

## Стек
- Python 3.12 + FastAPI + uvicorn
- httpx + BeautifulSoup4 (парсинг)
- asyncpg + PostgreSQL (кеш)
- Docker (деплой через Dokploy)

## API

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/` | Health check |
| GET | `/company/inn/{inn}` | Поиск по ИНН |
| GET | `/company/ogrn/{ogrn}` | Поиск по ОГРН |
| GET | `/search?q={name}` | Поиск по названию |
| GET | `/stats` | Статистика кеша |

## Параметры

- `?force=true` — принудительный парсинг, игнорируя кеш

## Данные организации

ИНН, КПП, ОГРН, название, полное название, статус, адрес, регион, руководитель, должность, ОКВЭД, ОКПО, ОКТМО, ОКАТО, ОКФС, ОКОГУ, уставный капитал, дата регистрации.

## Environment Variables

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=
CACHE_TTL_HOURS=24
REQUEST_DELAY=2.5
```
