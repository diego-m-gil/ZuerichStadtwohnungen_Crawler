import requests
from bs4 import BeautifulSoup
import sqlite3
import os
import asyncio
import logging
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
from telegram.ext import ApplicationBuilder
from datetime import datetime
import hashlib
import html
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
# python-telegram-bot uses httpx; at INFO it logs every API URL including the bot token.
for _noisy in ('httpx', 'httpcore'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
STZH_USERNAME = os.getenv('STZH_USERNAME')
STZH_PASSWORD = os.getenv('STZH_PASSWORD')

# Note: the listings live on the non-www host (emonitor "melon.rent" platform).
# www and non-www are different hosts and do NOT share the login session cookies.
BASE_URL = "https://vermietungen.stadt-zuerich.ch"
# Listings now come from a JSON API (the site renders them client-side); the old
# /publication/apartment/ HTML page is legacy and always empty.
API_URL = f"{BASE_URL}/api/public/objects/apartments-list?objecttype=apartment"
# Any protected path triggers the OIDC login redirect; we use it to start the login flow.
LOGIN_START_URL = f"{BASE_URL}/protected_access/"
DB_PATH = 'apartments.db'
MESSAGE_DELAY_SECONDS = 3

# When set (DRY_RUN=1), the crawler does everything EXCEPT send/delete/edit Telegram
# messages. It logs what it WOULD do. Used for safe live tests without notifying users.
DRY_RUN = os.getenv('DRY_RUN', '').strip() in ('1', 'true', 'True', 'yes')

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def _create_apartments_table(cursor):
    cursor.execute('''
        CREATE TABLE apartments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT,
            rentalgross TEXT,
            rooms TEXT,
            floor TEXT,
            area TEXT,
            move_in_date TEXT,
            zone TEXT,
            link TEXT,
            timestamp TEXT,
            unique_hash TEXT,
            telegram_message_id INTEGER,
            grundriss TEXT
        )
    ''')


def create_db():
    """Create or migrate the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apartments'")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(apartments)")
        columns = {col[1] for col in cursor.fetchall()}
        if 'telegram_message_id' not in columns:
            logger.info("Migrating database: adding telegram_message_id, removing unique_hash UNIQUE constraint")
            cursor.execute('ALTER TABLE apartments RENAME TO apartments_old')
            _create_apartments_table(cursor)
            cursor.execute('''
                INSERT INTO apartments
                    (id, address, rentalgross, rooms, floor, area,
                     move_in_date, zone, link, timestamp, unique_hash)
                SELECT id, address, rentalgross, rooms, floor, area,
                       move_in_date, zone,
                       CASE WHEN link = 'No link' THEN NULL ELSE link END,
                       timestamp, unique_hash
                FROM apartments_old
            ''')
            cursor.execute('DROP TABLE apartments_old')
            deleted = cursor.execute('''
                DELETE FROM apartments WHERE link IS NOT NULL AND id NOT IN (
                    SELECT MAX(id) FROM apartments WHERE link IS NOT NULL GROUP BY link
                )
            ''').rowcount
            if deleted:
                logger.info("Cleaned up %d duplicate records during migration", deleted)

            cursor.execute('SELECT id, address, rentalgross, rooms, floor, area, move_in_date, zone FROM apartments')
            for row in cursor.fetchall():
                new_hash = generate_content_hash(row[1], row[2], row[3], row[4], row[5], row[6], row[7])
                cursor.execute('UPDATE apartments SET unique_hash = ? WHERE id = ?', (new_hash, row[0]))
            logger.info("Recalculated content hashes for all existing records")
            logger.info("Database migration complete")

        # Incremental migration: add grundriss (floor plan link) column if missing.
        cursor.execute("PRAGMA table_info(apartments)")
        columns = {col[1] for col in cursor.fetchall()}
        if 'grundriss' not in columns:
            cursor.execute('ALTER TABLE apartments ADD COLUMN grundriss TEXT')
            logger.info("Added grundriss column")
    else:
        _create_apartments_table(cursor)

    conn.commit()
    conn.close()


def _normalize_field(s):
    if not s or not isinstance(s, str):
        return (s or '').strip()
    s = s.strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def normalize_listing_url(url):
    """Canonical form so the same ad matches even if href differs slightly."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url or url == 'No link':
        return None
    try:
        p = urlparse(url)
        scheme = (p.scheme or 'https').lower()
        netloc = (p.netloc or '').lower()
        if not netloc:
            return None
        path = (p.path or '').rstrip('/') or '/'
        q = sorted(parse_qsl(p.query, keep_blank_values=True))
        query = urlencode(q)
        return urlunparse((scheme, netloc, path, '', query, ''))
    except Exception:
        return url


def generate_content_hash(*fields):
    """Hash the given listing fields to detect any content change.

    Accepts a variable number of fields so the same function works for both the
    legacy 7-column migration recalculation and the richer API-based fields.
    """
    content = '|'.join(
        _normalize_field(x if isinstance(x, str) else ('' if x is None else str(x)))
        for x in fields
    )
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def normalize_existing_db_links():
    """Align stored links with canonical form so scraped rows match DB rows."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, link FROM apartments WHERE link IS NOT NULL')
    for row_id, link in cursor.fetchall():
        n = normalize_listing_url(link)
        if n and n != link:
            cursor.execute('UPDATE apartments SET link = ? WHERE id = ?', (n, row_id))
    deleted = cursor.execute('''
        DELETE FROM apartments WHERE link IS NOT NULL AND id NOT IN (
            SELECT MAX(id) FROM apartments WHERE link IS NOT NULL GROUP BY link
        )
    ''').rowcount
    if deleted:
        logger.info("Removed %d duplicate rows after link normalization", deleted)
    conn.commit()
    conn.close()


def login_session():
    """Log in via the email/password OIDC form flow and return an authenticated session.

    Returns the session (authenticated if credentials worked). Never logs credentials.
    """
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT, 'Accept-Language': 'de-CH,de;q=0.9'})

    if not STZH_USERNAME or not STZH_PASSWORD:
        logger.warning("STZH_USERNAME / STZH_PASSWORD not set — fetching without login")
        return session

    try:
        r = session.get(LOGIN_START_URL, timeout=30)
        if 'login.stadt-zuerich.ch' not in r.url:
            logger.info("No login redirect encountered (data may be public); continuing")
            return session

        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form', attrs={'name': 'Email'})
        if not form:
            for f in soup.find_all('form'):
                if f.find('input', attrs={'name': 'password'}):
                    form = f
                    break
        if not form:
            logger.error("Login form not found on login page — proceeding unauthenticated")
            return session

        action = urljoin(r.url, form.get('action', 'auth'))
        payload = {}
        for inp in form.find_all('input'):
            name = inp.get('name')
            if name:
                payload[name] = inp.get('value', '')
        payload['userid'] = STZH_USERNAME
        payload['password'] = STZH_PASSWORD
        payload.setdefault('logins', 'email')

        r2 = session.post(action, data=payload, timeout=30)
        if 'login.stadt-zuerich.ch' in r2.url:
            logger.error("Login did not complete (still on login host) — check credentials/2FA")
        else:
            logger.info("Login successful")
    except Exception as e:
        logger.error("Login flow error: %s", e)

    return session


def _fmt_num(x):
    """Render API numbers cleanly: 45.0 -> '45', 1.5 -> '1.5', None -> ''."""
    if x is None or x == '':
        return ''
    try:
        return f'{float(x):g}'
    except (ValueError, TypeError):
        return str(x)


def _is_public_url(u):
    """True only for links on publicly-resolvable hosts.

    The API sometimes returns internal storage URLs (e.g. host ending in `.szh.loc`
    on port 9021) that resolve only inside the city network — those are useless to
    subscribers, so we must not post them.
    """
    if not u or not isinstance(u, str) or not u.startswith('http'):
        return False
    host = (urlparse(u).hostname or '').lower()
    if not host or '.' not in host:
        return False
    if host in ('localhost',) or host.endswith('.loc') or host.endswith('.local') or host.endswith('.internal'):
        return False
    return True


def _extract_apply_link(item):
    """The stable per-listing link (embeds the uuid) used as the dedup key."""
    html = item.get('apply_button') or ''
    if html:
        a = BeautifulSoup(html, 'html.parser').find('a', href=True)
        if a and a['href']:
            return a['href']
    uuid = item.get('uuid')
    if uuid:
        return f"{BASE_URL}/form/application/new?uuids={uuid}"
    return None


def fetch_apartments(session=None):
    """Fetch apartment listings from the site's JSON API (authenticated session required)."""
    logger.info("Fetching apartments from API %s", API_URL)
    getter = session.get if session is not None else requests.get
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': f'{BASE_URL}/de/',
    }
    response = getter(API_URL, headers=headers, timeout=45)
    response.raise_for_status()

    ctype = response.headers.get('Content-Type', '')
    if 'json' not in ctype:
        logger.error(
            "API did not return JSON (Content-Type=%s) — likely not authenticated. "
            "First 200 chars: %s",
            ctype, response.text[:200].replace('\n', ' '),
        )
        return []
    try:
        items = response.json()
    except ValueError as e:
        logger.error("Failed to parse API JSON: %s", e)
        return []
    if not isinstance(items, list):
        logger.error("Unexpected API JSON shape: %s", type(items).__name__)
        return []

    apartments = []
    for item in items:
        try:
            street = _normalize_field(item.get('street_and_number') or '')
            city = _normalize_field(item.get('postcode_and_city') or '')
            address = ', '.join(p for p in (street, city) if p) or 'Keine Angabe'
            building = _normalize_field(item.get('building') or '')
            property_type = _normalize_field(item.get('property_type') or item.get('object_type_name') or '')
            rooms = _fmt_num(item.get('rooms'))
            area = _fmt_num(item.get('area'))
            floor = _normalize_field(item.get('floor') or '')
            move_in_date = _normalize_field(item.get('move_in_date') or '')
            rentalgross = _fmt_num(item.get('rentalgross'))
            rentalgross_net = _fmt_num(item.get('rentalgross_net'))
            incidental_costs = _fmt_num(item.get('incidental_costs'))
            price_m2 = _fmt_num(item.get('rentalprice_squaremeter'))
            reference = _normalize_field(item.get('reference_number') or '')

            link = normalize_listing_url(_extract_apply_link(item))

            layout = item.get('layout_plan') or ''
            grundriss = layout if _is_public_url(layout) else None
            tour = item.get('virtual_tour_link') or ''
            virtual_tour = tour if _is_public_url(tour) else None

            content_hash = generate_content_hash(
                address, building, property_type, rooms, area, floor,
                move_in_date, rentalgross, rentalgross_net, incidental_costs,
            )

            apartments.append({
                'address': address,
                'building': building,
                'property_type': property_type,
                'rooms': rooms,
                'area': area,
                'floor': floor,
                'move_in_date': move_in_date,
                'rentalgross': rentalgross,
                'rentalgross_net': rentalgross_net,
                'incidental_costs': incidental_costs,
                'price_m2': price_m2,
                'reference': reference,
                'zone': city,  # retained for DB schema/back-compat
                'link': link,
                'grundriss': grundriss,
                'virtual_tour': virtual_tour,
                'content_hash': content_hash,
            })
        except Exception as e:
            logger.warning("Error processing listing item: %s", e)
            continue

    logger.info("Fetched %d apartments", len(apartments))
    return apartments


def detect_changes(apartments):
    """Compare scraped apartments against the database.

    Returns:
        new_apts: list of apartment dicts not yet in the DB
        updated_apts: list of (apt_dict, old_telegram_message_id, db_row_id)
            for ads whose link exists but content changed
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    new_apts = []
    updated_apts = []

    for apt in apartments:
        link = apt['link']

        if not link:
            cursor.execute('SELECT id FROM apartments WHERE unique_hash = ?', (apt['content_hash'],))
            if cursor.fetchone() is None:
                new_apts.append(apt)
            continue

        cursor.execute(
            'SELECT id, unique_hash, telegram_message_id FROM apartments WHERE link = ? ORDER BY id DESC LIMIT 1',
            (link,)
        )
        existing = cursor.fetchone()

        if existing is None:
            new_apts.append(apt)
        elif existing[1] != apt['content_hash']:
            old_msg_id = existing[2]
            if old_msg_id is None:
                logger.warning(
                    "Content changed for link %s but telegram_message_id is missing — "
                    "cannot delete old channel message (post may predate tracking or send failed). "
                    "Ensure the bot is channel admin with delete rights.",
                    link[:80] + '...' if link and len(link) > 80 else link,
                )
            updated_apts.append((apt, old_msg_id, existing[0]))

    conn.close()
    return new_apts, updated_apts


def save_apartment(apt, telegram_message_id=None):
    """Insert a new apartment into the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO apartments
           (address, rentalgross, rooms, floor, area, move_in_date,
            zone, link, timestamp, unique_hash, telegram_message_id, grundriss)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
         apt['area'], apt['move_in_date'], apt['zone'], apt['link'],
         datetime.now().isoformat(), apt['content_hash'], telegram_message_id,
         apt.get('grundriss'))
    )
    conn.commit()
    conn.close()


def update_apartment(db_id, apt, telegram_message_id=None):
    """Update an existing apartment record with corrected content."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''UPDATE apartments
           SET address=?, rentalgross=?, rooms=?, floor=?, area=?,
               move_in_date=?, zone=?, timestamp=?, unique_hash=?,
               telegram_message_id=?, grundriss=?
           WHERE id=?''',
        (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
         apt['area'], apt['move_in_date'], apt['zone'],
         datetime.now().isoformat(), apt['content_hash'],
         telegram_message_id, apt.get('grundriss'), db_id)
    )
    conn.commit()
    conn.close()


def format_apartment_message(apt):
    """Build the Telegram message (HTML parse mode) with clean tappable links."""
    def esc(x):
        return html.escape(str(x)) if x else ''

    lines = [
        f"Neue Stadtwohnung gefunden am {datetime.now().strftime('%d.%m.%Y')}:",
        "",
        f"Adresse: {esc(apt['address'])}",
    ]
    if apt.get('building'):
        lines.append(f"Siedlung: {esc(apt['building'])}")
    if apt.get('property_type'):
        lines.append(f"Typ: {esc(apt['property_type'])}")
    if apt.get('rooms'):
        lines.append(f"Zimmer: {esc(apt['rooms'])}")
    if apt.get('area'):
        lines.append(f"Fläche: {esc(apt['area'])} m²")
    if apt.get('floor'):
        lines.append(f"Stockwerk: {esc(apt['floor'])}")

    rent = f"Bruttomiete: {esc(apt['rentalgross'])} CHF"
    if apt.get('rentalgross_net') and apt.get('incidental_costs'):
        rent += f" (Netto {esc(apt['rentalgross_net'])} + Nebenkosten {esc(apt['incidental_costs'])})"
    lines.append(rent)

    if apt.get('move_in_date'):
        lines.append(f"Vermietung ab: {esc(apt['move_in_date'])}")

    lines.append("")
    link_parts = []
    if apt.get('link'):
        link_parts.append(f'<a href="{html.escape(apt["link"], quote=True)}">Jetzt bewerben</a>')
    else:
        link_parts.append('Bewerbung: Nicht verfügbar')
    if apt.get('grundriss'):
        link_parts.append(f'<a href="{html.escape(apt["grundriss"], quote=True)}">Grundriss (PDF)</a>')
    if apt.get('virtual_tour'):
        link_parts.append(f'<a href="{html.escape(apt["virtual_tour"], quote=True)}">360°-Rundgang</a>')
    lines.append("  ·  ".join(link_parts))

    return "\n".join(lines)


async def send_telegram_message(app, apt):
    """Send an apartment listing to Telegram. Returns the message_id or None."""
    message = format_apartment_message(apt)

    for attempt in range(3):
        try:
            sent = await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='HTML')
            logger.info("Message sent for: %s (msg_id=%d)", apt['address'], sent.message_id)
            return sent.message_id
        except Exception as e:
            logger.warning("Send attempt %d/3 failed: %s", attempt + 1, e)
            await asyncio.sleep(5)

    logger.error("Failed to send message for: %s after 3 attempts", apt['address'])
    return None


async def delete_or_edit_telegram_message(app, message_id, replacement_text):
    """Delete a previously sent message, or edit it in place if deletion fails (e.g. >48h old).

    Telegram does not allow bots to delete messages older than ~48 hours even as channel admin.
    Editing has no such time limit, so we fall back to editing to avoid leaving wrong content visible.
    """
    try:
        await app.bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_id)
        logger.info("Deleted outdated message (msg_id=%d)", message_id)
        return 'deleted'
    except Exception as del_err:
        logger.warning(
            "Could not delete message %d (%s) — falling back to edit in place",
            message_id, del_err,
        )
        try:
            await app.bot.edit_message_text(
                chat_id=TELEGRAM_CHAT_ID,
                message_id=message_id,
                text=replacement_text,
                parse_mode='HTML',
            )
            logger.info("Edited outdated message in place (msg_id=%d)", message_id)
            return 'edited'
        except Exception as edit_err:
            logger.error(
                "Could not delete OR edit message %d: delete=%s edit=%s",
                message_id, del_err, edit_err,
            )
            return 'failed'


async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env or environment")
        return

    if DRY_RUN:
        logger.info("DRY_RUN enabled — no Telegram messages will be sent, deleted, or edited")

    create_db()
    normalize_existing_db_links()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).connect_timeout(30).read_timeout(30).build()

    session = login_session()
    apartments = fetch_apartments(session)
    if not apartments:
        logger.info("No apartments found on the website")
        return

    new_apts, updated_apts = detect_changes(apartments)
    logger.info("Results: %d new, %d updated, %d unchanged",
                len(new_apts), len(updated_apts),
                len(apartments) - len(new_apts) - len(updated_apts))

    if not new_apts and not updated_apts:
        logger.info("Nothing to do")
        return

    if DRY_RUN:
        for apt in new_apts:
            logger.info("[DRY_RUN] would POST new listing:\n%s", format_apartment_message(apt))
        for apt, old_msg_id, _db_id in updated_apts:
            logger.info("[DRY_RUN] would REPLACE msg_id=%s for updated listing:\n%s",
                        old_msg_id, format_apartment_message(apt))
        logger.info("[DRY_RUN] done — DB left unchanged, no messages sent")
        return

    await app.initialize()
    try:
        all_actions = []
        for apt, old_msg_id, db_id in updated_apts:
            all_actions.append(('update', apt, old_msg_id, db_id))
        for apt in new_apts:
            all_actions.append(('new', apt, None, None))

        for i, (action, apt, old_msg_id, db_id) in enumerate(all_actions):
            if i > 0:
                await asyncio.sleep(MESSAGE_DELAY_SECONDS)

            if action == 'update':
                if old_msg_id:
                    replacement_text = format_apartment_message(apt)
                    outcome = await delete_or_edit_telegram_message(app, old_msg_id, replacement_text)
                    if outcome == 'edited':
                        # Old message was edited in place — no new post needed, keep same message_id
                        update_apartment(db_id, apt, old_msg_id)
                        continue
                    elif outcome == 'failed':
                        logger.error(
                            "Could not delete or edit old message %d for %s; "
                            "posting corrected version anyway (duplicate will remain).",
                            old_msg_id, apt['address'],
                        )
                else:
                    logger.warning(
                        "Posting corrected listing without removing old message (no stored message_id). "
                        "Subscribers may see duplicate until old post is removed manually."
                    )
                msg_id = await send_telegram_message(app, apt)
                if msg_id is None:
                    logger.error(
                        "Telegram send failed for update; DB not updated so next run can retry."
                    )
                    continue
                update_apartment(db_id, apt, msg_id)
            else:
                msg_id = await send_telegram_message(app, apt)
                if msg_id is None:
                    logger.error(
                        "Telegram send failed for new listing; not saving to DB so next run can retry."
                    )
                    continue
                save_apartment(apt, msg_id)
    finally:
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
