# Helper scripts

| Script | Purpose |
|--------|---------|
| `debug_crawler.py` | Scrape the portal and test dedup against `apartments_debug.db` (no Telegram). Writes `exports/website_debug.html` on demand. |
| `export_listings.py` | Export `apartments.db` to `exports/stadtwohnungen_export_YYYYMMDD.csv`. |
| `connect_server.bat` | Windows: ping + SSH test to the VPS (edit key path if needed). |

Run from the **repository root**:

```bash
python scripts/debug_crawler.py
python scripts/export_listings.py
```
