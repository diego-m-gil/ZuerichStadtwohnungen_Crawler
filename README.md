# Zürich Stadtwohnung Crawler

Automated web scraper that monitors apartment listings from Zürich's public housing portal and posts them to a Telegram channel: https://t.me/ZurichStadtwohnungenNeu

For more details about the listings and rental guidelines, visit the official website at [stadt-zuerich.ch/e-vermietung](https://www.stadt-zuerich.ch/e-vermietung).

## How It Works

The script runs on a schedule (via cron) and:

1. **Scrapes** apartment listings from [vermietungen.stadt-zuerich.ch](https://www.vermietungen.stadt-zuerich.ch/publication/apartment/)
2. **Compares** each listing against the SQLite database using the application link as the unique identifier
3. **Detects changes** — if an existing listing was corrected (price, address, etc.), the old Telegram message is deleted and a new one is posted
4. **Posts new listings** to the Telegram channel with address, rent, rooms, floor, area, move-in date, zone, and a direct application link
5. **Stores** each listing with its Telegram message ID so corrections can be handled cleanly

## Tech Stack

- **Python 3.8+**
- **BeautifulSoup** — HTML parsing
- **SQLite** — local database
- **python-telegram-bot** — Telegram Bot API (v20+, async)
- **python-dotenv** — environment variable loading

## Setup

### Clone & Install

```bash
git clone https://github.com/yourusername/ZuerichStadtwohnungen_Crawler.git
cd ZuerichStadtwohnungen_Crawler
pip install -r requirements.txt
```

### Configuration

Copy `.env.template` to `.env` and fill in your credentials:

```bash
cp .env.template .env
```

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_channel_chat_id
```

The bot must be an **admin** of the Telegram channel with permission to post and delete messages.

### Run

```bash
python ZuerichStadtwohnungen_Crawler.py
```

### Schedule with Cron

```bash
*/15 * * * * cd /home/ubuntu/ZuerichStadtwohnungen_Crawler && /home/ubuntu/ZuerichStadtwohnungen_Crawler/venv/bin/python ZuerichStadtwohnungen_Crawler.py >> crawler.log 2>&1
```

### Debug

Run the debug script to test scraping and deduplication without sending Telegram messages:

```bash
python debug_crawler.py
```

## Project Files

| File | Purpose |
|------|---------|
| `ZuerichStadtwohnungen_Crawler.py` | Main crawler + Telegram posting |
| `debug_crawler.py` | Debug scraping & DB logic (no Telegram) |
| `requirements.txt` | Python dependencies |
| `.env.template` | Template for environment variables |
| `apartments.db` | SQLite database (auto-created) |

## License

MIT
