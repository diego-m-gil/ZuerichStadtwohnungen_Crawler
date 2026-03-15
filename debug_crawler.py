#!/usr/bin/env python3
"""
Debug script for ZuerichStadtwohnungen_Crawler.
Tests scraping and DB logic without sending Telegram messages.
"""

import requests
from bs4 import BeautifulSoup
import sqlite3
import os
from datetime import datetime
import hashlib
from dotenv import load_dotenv

load_dotenv()

URL = "https://www.vermietungen.stadt-zuerich.ch/publication/apartment/"
DB_PATH = 'apartments_debug.db'


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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apartments'")
    if not cursor.fetchone():
        _create_apartments_table(cursor)
    conn.commit()
    conn.close()


def generate_content_hash(address, rentalgross, rooms, floor, area, move_in_date, zone):
    content = f"{address}|{rentalgross}|{rooms}|{floor}|{area}|{move_in_date}|{zone}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def test_website_connection():
    print("Testing website connection...")
    try:
        response = requests.get(URL, timeout=10)
        print(f"  Status Code: {response.status_code}")
        print(f"  Content Length: {len(response.text)} bytes")
        with open('website_debug.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        print("  Saved HTML to website_debug.html")
        return response
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def analyze_html_structure(response):
    if not response:
        return
    print("\nAnalyzing HTML structure...")
    soup = BeautifulSoup(response.text, 'html.parser')
    tables = soup.find_all('table')
    print(f"  Tables found: {len(tables)}")

    if tables:
        tbody = tables[0].find('tbody')
        if tbody:
            rows = tbody.find_all('tr')
            print(f"  Rows in first table: {len(rows)}")
            if rows:
                cells = rows[0].find_all('td')
                print(f"  Cells in first row: {len(cells)}")
                for i, cell in enumerate(cells):
                    print(f"    Cell {i}: class={cell.get('class', [])}, text='{cell.text.strip()[:60]}'")

    expected = ['publicated_adress', 'rentalgross', 'rooms', 'floor', 'area', 'move_in_date', 'metropolitan']
    print("\n  CSS class check:")
    for cls in expected:
        count = len(soup.find_all(class_=cls))
        status = "OK" if count > 0 else "MISSING"
        print(f"    {cls}: {count} elements [{status}]")


def fetch_apartments_debug():
    print("\nFetching apartments (debug)...")
    try:
        response = requests.get(URL, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        apartments = []

        for i, row in enumerate(soup.select('table tbody tr')):
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

                apt = {
                    'address': address, 'rentalgross': rentalgross, 'rooms': rooms,
                    'floor': floor, 'area': area, 'move_in_date': move_in_date,
                    'zone': zone, 'link': link, 'content_hash': content_hash,
                }
                apartments.append(apt)

                print(f"\n  Apartment {i+1}:")
                print(f"    Address:  {address}")
                print(f"    Rent:     {rentalgross}")
                print(f"    Rooms:    {rooms}")
                print(f"    Floor:    {floor}")
                print(f"    Area:     {area}")
                print(f"    Move-in:  {move_in_date}")
                print(f"    Zone:     {zone}")
                print(f"    Link:     {link}")
                print(f"    Hash:     {content_hash}")

            except Exception as e:
                print(f"  Row {i+1} ERROR: {e}")
                continue

        print(f"\nTotal apartments scraped: {len(apartments)}")
        return apartments

    except Exception as e:
        print(f"Fetch error: {e}")
        return []


def test_dedup_logic(apartments):
    """Simulate the detect_changes logic against the debug DB."""
    print("\nTesting deduplication logic...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    new_count = 0
    updated_count = 0
    unchanged_count = 0

    for apt in apartments:
        link = apt['link']
        if not link:
            cursor.execute('SELECT id FROM apartments WHERE unique_hash = ?', (apt['content_hash'],))
            if cursor.fetchone() is None:
                new_count += 1
                cursor.execute(
                    '''INSERT INTO apartments
                       (address, rentalgross, rooms, floor, area, move_in_date,
                        zone, link, timestamp, unique_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
                     apt['area'], apt['move_in_date'], apt['zone'], apt['link'],
                     datetime.now().isoformat(), apt['content_hash'])
                )
            else:
                unchanged_count += 1
            continue

        cursor.execute(
            'SELECT id, unique_hash, telegram_message_id FROM apartments WHERE link = ?', (link,)
        )
        existing = cursor.fetchone()

        if existing is None:
            new_count += 1
            cursor.execute(
                '''INSERT INTO apartments
                   (address, rentalgross, rooms, floor, area, move_in_date,
                    zone, link, timestamp, unique_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
                 apt['area'], apt['move_in_date'], apt['zone'], apt['link'],
                 datetime.now().isoformat(), apt['content_hash'])
            )
        elif existing[1] != apt['content_hash']:
            updated_count += 1
            old_msg_id = existing[2]
            print(f"  UPDATED: {apt['address']} (old msg_id={old_msg_id})")
            cursor.execute(
                '''UPDATE apartments
                   SET address=?, rentalgross=?, rooms=?, floor=?, area=?,
                       move_in_date=?, zone=?, timestamp=?, unique_hash=?
                   WHERE id=?''',
                (apt['address'], apt['rentalgross'], apt['rooms'], apt['floor'],
                 apt['area'], apt['move_in_date'], apt['zone'],
                 datetime.now().isoformat(), apt['content_hash'], existing[0])
            )
        else:
            unchanged_count += 1

    conn.commit()
    conn.close()

    print(f"\n  New:       {new_count}")
    print(f"  Updated:   {updated_count}")
    print(f"  Unchanged: {unchanged_count}")


def main():
    print("=== ZuerichStadtwohnungen Crawler - Debug ===")
    print(f"URL: {URL}")
    print(f"DB:  {DB_PATH}\n")

    response = test_website_connection()
    if not response:
        print("Cannot proceed — connection failed.")
        return

    analyze_html_structure(response)
    create_db()
    apartments = fetch_apartments_debug()

    if apartments:
        test_dedup_logic(apartments)
        print("\nRun again immediately to verify 0 new / 0 updated (all unchanged).")
    else:
        print("\nNo apartments found — check HTML structure above.")


if __name__ == "__main__":
    main()
