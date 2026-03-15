import requests
from bs4 import BeautifulSoup
import sqlite3
import os
import asyncio
import logging
from telegram.ext import ApplicationBuilder
from datetime import datetime
import hashlib
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
URL = "https://www.vermietungen.stadt-zuerich.ch/publication/apartment/"
DB_PATH = 'apartments.db'
MESSAGE_DELAY_SECONDS = 3


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
            telegram_message_id INTEGER
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
    else:
        _create_apartments_table(cursor)

    conn.commit()
    conn.close()


def generate_content_hash(address, rentalgross, rooms, floor, area, move_in_date, zone):
    """Hash all listing fields to detect any content change."""
    content = f"{address}|{rentalgross}|{rooms}|{floor}|{area}|{move_in_date}|{zone}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def fetch_apartments():
    """Scrape apartment listings from the website."""
    logger.info("Fetching apartments from %s", URL)
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    apartments = []
    for row in soup.select('table tbody tr'):
        try:
            address = row.find('td', class_='publicated_adress')
            address = address.text.strip() if address else 'Keine Angabe'

            rentalgross = row.find('td', class_='rentalgross')
            rentalgross = rentalgross.text.strip() if rentalgross else 'Keine Angabe'

            rooms = row.find('td', class_='rooms')
            rooms = rooms.text.strip() if rooms else 'Keine Angabe'

            floor = row.find('td', class_='floor')
            floor = floor.text.strip() if floor else 'Keine Angabe'

            area = row.find('td', class_='area')
            area = area.text.strip() if area else 'Keine Angabe'

            move_in_date = row.find('td', class_='move_in_date')
            move_in_date = move_in_date.text.strip() if move_in_date else 'Keine Angabe'

            zone = row.find('td', class_='metropolitan')
            zone = zone.text.strip() if zone else 'Keine Angabe'

            link_elem = row.find('a', class_='apply_button')
            link = f"https://www.vermietungen.stadt-zuerich.ch{link_elem['href']}" if link_elem else None

            content_hash = generate_content_hash(address, rentalgross, rooms, floor, area, move_in_date, zone)

            apartments.append({
                'address': address,
                'rentalgross': rentalgross,
                'rooms': rooms,
                'floor': floor,
                'area': area,
                'move_in_date': move_in_date,
                'zone': zone,
                'link': link,
                'content_hash': content_hash,
            })
        except Exception as e:
            logger.warning("Error processing row: %s", e)
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
            updated_apts.append((apt, existing[2], existing[0]))

    conn.close()
    return new_apts, updated_apts


def save_apartment(apt, telegram_message_id=None):
    """Insert a new apartment into the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO apartments
           (address, rentalgross, rooms, floor, area, move_in_date,
            zone, link, timestamp, unique_hash, telegram_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
         apt['area'], apt['move_in_date'], apt['zone'], apt['link'],
         datetime.now().isoformat(), apt['content_hash'], telegram_message_id)
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
               telegram_message_id=?
           WHERE id=?''',
        (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
         apt['area'], apt['move_in_date'], apt['zone'],
         datetime.now().isoformat(), apt['content_hash'],
         telegram_message_id, db_id)
    )
    conn.commit()
    conn.close()


async def send_telegram_message(app, apt):
    """Send an apartment listing to Telegram. Returns the message_id or None."""
    link_text = apt['link'] or 'Nicht verfügbar'
    message = (
        f"Neue Stadtwohnung gefunden am {datetime.now().strftime('%d.%m.%Y')}:\n\n"
        f"Adresse: {apt['address']}\n"
        f"Zone: Zürich, {apt['zone']}\n"
        f"Bruttomiete: {apt['rentalgross']} CHF\n"
        f"Zimmer: {apt['rooms']}\n"
        f"Stockwerk: {apt['floor']}\n"
        f"Fläche: {apt['area']}\n"
        f"Vermietung ab: {apt['move_in_date']}\n"
        f"Direktlink Bewerbung: {link_text}"
    )

    for attempt in range(3):
        try:
            sent = await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            logger.info("Message sent for: %s (msg_id=%d)", apt['address'], sent.message_id)
            return sent.message_id
        except Exception as e:
            logger.warning("Send attempt %d/3 failed: %s", attempt + 1, e)
            await asyncio.sleep(5)

    logger.error("Failed to send message for: %s after 3 attempts", apt['address'])
    return None


async def delete_telegram_message(app, message_id):
    """Delete a previously sent message from the Telegram channel."""
    try:
        await app.bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_id)
        logger.info("Deleted outdated message (msg_id=%d)", message_id)
        return True
    except Exception as e:
        logger.warning("Could not delete message %d: %s", message_id, e)
        return False


async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env or environment")
        return

    create_db()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).connect_timeout(30).read_timeout(30).build()

    apartments = fetch_apartments()
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
                    await delete_telegram_message(app, old_msg_id)
                msg_id = await send_telegram_message(app, apt)
                update_apartment(db_id, apt, msg_id)
            else:
                msg_id = await send_telegram_message(app, apt)
                save_apartment(apt, msg_id)
    finally:
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
