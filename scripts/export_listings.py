#!/usr/bin/env python3
"""
Export apartment rows from the crawler SQLite DB to CSV for sharing or analysis.

Usage (from repo root):
  python scripts/export_listings.py
  python scripts/export_listings.py --db path/to/apartments.db -o path/out.csv
"""

import argparse
import csv
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORTS_DIR = ROOT / 'exports'
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# UTF-8 BOM helps Excel on Windows open umlauts correctly
ENCODING = 'utf-8-sig'


def main():
    parser = argparse.ArgumentParser(description='Export Stadtwohnungen listings to CSV')
    parser.add_argument(
        '--db',
        default=None,
        help='Path to SQLite database (default: <repo>/apartments.db)',
    )
    parser.add_argument(
        '-o', '--output',
        default=None,
        help='Output CSV path (default: exports/stadtwohnungen_export_YYYYMMDD.csv)',
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else ROOT / 'apartments.db'
    if not db_path.is_file():
        raise SystemExit(f'Database not found: {db_path.resolve()}')

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = EXPORTS_DIR / f'stadtwohnungen_export_{datetime.now().strftime("%Y%m%d")}.csv'

    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT
            address AS adresse,
            rentalgross AS bruttomiete_chf,
            rooms AS zimmer,
            floor AS stockwerk,
            area AS flaeche,
            move_in_date AS bezug_ab,
            zone,
            link AS bewerbungslink,
            timestamp AS erfasst_utc
        FROM apartments
        ORDER BY id
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        raise SystemExit('No rows in database.')

    fieldnames = list(rows[0].keys())
    with out_path.open('w', newline='', encoding=ENCODING) as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow(dict(row))

    print(f'Exported {len(rows)} rows to {out_path.resolve()}')


if __name__ == '__main__':
    main()
