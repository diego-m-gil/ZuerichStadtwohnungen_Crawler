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

1. **Fetches** current listings from the portal's JSON API (`/api/public/objects/apartments-list`). The site renders listings client-side and gates the API behind a login, so the crawler performs an email/password (OIDC) login first.
2. **Compares** each listing against the SQLite database using the application link (which embeds the listing's stable UUID) as the unique identifier
3. **Detects changes** — if an existing listing was corrected (price, address, etc.), the bot deletes or edits the old Telegram message and posts/updates as needed
4. **Posts new listings** to the Telegram channel with address, building (Siedlung), type, rooms, area, floor, gross rent (with net + Nebenkosten split), move-in date, and tappable links to the application page and the floor-plan PDF where available
5. **Stores** each listing with its Telegram message ID so corrections can be handled cleanly

Set `DRY_RUN=1` in the environment to run the full pipeline (login, fetch, dedup, formatting) **without** sending, deleting, or editing any Telegram messages — useful for safe testing.

## Tech stack

- **Python 3.8+**
- **requests** — authenticated HTTP session + login flow, JSON API calls
- **BeautifulSoup** — parsing the login form (and legacy HTML)
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
STZH_USERNAME=your_stadt-zuerich_portal_email
STZH_PASSWORD=your_stadt-zuerich_portal_password
```

`STZH_USERNAME` / `STZH_PASSWORD` are the email/password login for the city's rental portal (`vermietungen.stadt-zuerich.ch`); the API requires an authenticated session. Keep `.env` out of version control (it is gitignored) and readable only by the owner (`chmod 600 .env` on the server).

The bot must be an **admin** of the Telegram channel with permission to post, delete, and edit messages.

### Run

```bash
python ZuerichStadtwohnungen_Crawler.py
```

### Cron (Friday-focused; times are the server's local timezone)

The city posts almost exclusively on Fridays, so the schedule checks frequently on Fridays and lightly the rest of the week (this also avoids hammering the login flow):

```bash
*/15 7-18 * * 5   cd /home/ubuntu/ZuerichStadtwohnungen_Crawler && /path/to/venv/bin/python ZuerichStadtwohnungen_Crawler.py >> logs/crawler.log 2>&1   # Fri 07–18h, every 15 min
0 8-19 * * 1-4    cd /home/ubuntu/ZuerichStadtwohnungen_Crawler && /path/to/venv/bin/python ZuerichStadtwohnungen_Crawler.py >> logs/crawler.log 2>&1   # Mon–Thu, hourly
0 10,17 * * 0,6   cd /home/ubuntu/ZuerichStadtwohnungen_Crawler && /path/to/venv/bin/python ZuerichStadtwohnungen_Crawler.py >> logs/crawler.log 2>&1   # Sat/Sun, twice
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
