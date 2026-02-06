# rusprofile-parser

REST API сервис для получения данных организаций с rusprofile.ru.

## Стек
- Python 3.12 + FastAPI + uvicorn
- httpx + BeautifulSoup4 (парсинг)
- asyncpg + PostgreSQL (кеш)
- Docker

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

| Переменная | Описание |
|-----------|----------|
| `DB_HOST` | Хост PostgreSQL |
| `DB_PORT` | Порт PostgreSQL |
| `DB_NAME` | Имя базы данных |
| `DB_USER` | Пользователь БД |
| `DB_PASSWORD` | Пароль БД |
| `CACHE_TTL_HOURS` | Время жизни кеша в часах (по умолчанию 24) |
| `REQUEST_DELAY` | Задержка между запросами к rusprofile в секундах (по умолчанию 2.5) |
