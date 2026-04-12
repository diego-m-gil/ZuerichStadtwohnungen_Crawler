#!/usr/bin/env python3
"""
Export apartment rows from the crawler SQLite DB to CSV for sharing or analysis.

Usage:
  python export_listings.py
  python export_listings.py --db /path/to/apartments.db -o my_export.csv
"""

import argparse
import csv
import sqlite3
from datetime import datetime
from pathlib import Path

# UTF-8 BOM helps Excel on Windows open umlauts correctly
ENCODING = 'utf-8-sig'


def main():
    parser = argparse.ArgumentParser(description='Export Stadtwohnungen listings to CSV')
    parser.add_argument('--db', default='apartments.db', help='Path to SQLite database')
    parser.add_argument(
        '-o', '--output',
        default=None,
        help='Output CSV path (default: stadtwohnungen_export_YYYYMMDD.csv)',
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f'Database not found: {db_path.resolve()}')

    out_path = Path(
        args.output
        or f'stadtwohnungen_export_{datetime.now().strftime("%Y%m%d")}.csv'
    )

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
