# Zürich Stadtwohnung Crawler

Automated web scraper that monitors apartment listings from Zürich's public housing portal and posts them to a Telegram channel: https://t.me/ZurichStadtwohnungenNeu

For more details about the listings and rental guidelines, visit the official website at [stadt-zuerich.ch/e-vermietung](https://www.stadt-zuerich.ch/e-vermietung).

## Repository layout

| Path | Purpose |
|------|---------|
| `ZuerichStadtwohnungen_Crawler.py` | Main crawler + Telegram (entry point for cron) |
| `requirements.txt` / `.env.template` | Dependencies and env template |
| `scripts/` | Debug scraper, CSV export, Windows SSH helper — see `scripts/README.md` |
| `docs/` | Notes for data exports (e.g. `EXPORT_HINWEISE.txt`) |
| `exports/` | Local output folder for CSV/HTML (gitignored contents; see `exports/README.md`) |
| `apartments.db` | SQLite DB (created at repo root when you run the main script) |

## How it works

The script runs on a schedule (via cron) and:

1. **Scrapes** apartment listings from [vermietungen.stadt-zuerich.ch](https://www.vermietungen.stadt-zuerich.ch/publication/apartment/)
2. **Compares** each listing against the SQLite database using the application link as the unique identifier
3. **Detects changes** — if an existing listing was corrected (price, address, etc.), the bot deletes or edits the old Telegram message and posts/updates as needed
4. **Posts new listings** to the Telegram channel with address, rent, rooms, floor, area, move-in date, zone, and a direct application link
5. **Stores** each listing with its Telegram message ID so corrections can be handled cleanly

## Tech stack

- **Python 3.8+**
- **BeautifulSoup** — HTML parsing
- **SQLite** — local database
- **python-telegram-bot** — Telegram Bot API (v20+, async)
- **python-dotenv** — environment variable loading

## Setup

### Clone and install

```bash
git clone https://github.com/diego-m-gil/ZuerichStadtwohnungen_Crawler.git
cd ZuerichStadtwohnungen_Crawler
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.template .env
```

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_channel_chat_id
```

The bot must be an **admin** of the Telegram channel with permission to post, delete, and edit messages.

### Run

```bash
python ZuerichStadtwohnungen_Crawler.py
```

### Cron (example)

```bash
*/15 * * * * cd /home/ubuntu/ZuerichStadtwohnungen_Crawler && /path/to/venv/bin/python ZuerichStadtwohnungen_Crawler.py >> logs/crawler.log 2>&1
```

Keep **`ZuerichStadtwohnungen_Crawler.py` at the repository root** on the server so existing cron paths keep working. You can still `git pull` the rest of the repo (including `scripts/`).

### Debug (no Telegram)

```bash
python scripts/debug_crawler.py
```

### Export listings to CSV

```bash
python scripts/export_listings.py
```

Default output: `exports/stadtwohnungen_export_YYYYMMDD.csv`. Pair with `docs/EXPORT_HINWEISE.txt` when sharing data.

## License

MIT
