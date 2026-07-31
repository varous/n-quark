#!/usr/bin/env python3
"""Reference incremental sync: n-quark events feed -> crawl-space's own DB.

Stdlib only (urllib + sqlite3), so it runs anywhere. It demonstrates the contract from
docs/events-feed.md: incremental pull via ``updated_since``, upsert by canonical ``id``,
and storing ``redistribution_tier`` so the UI can gate rendering (open = card, link_only =
link-out). Adapt ``upsert_event`` + the cursor store to crawl-space's real schema/migrations.

Usage:
    python tools/crawl_space_sync.py --gateway http://localhost:8000 --db crawlspace.sqlite

By default it pulls everything except the ``excluded`` tier (the feed's default). Pass
``--tier open`` to sync only re-hostable events.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request

PAGE = 200


def fetch_page(gateway: str, *, updated_since: str | None, tier: str | None, offset: int) -> dict:
    params = {"limit": PAGE, "offset": offset}
    if updated_since:
        params["updated_since"] = updated_since
    if tier:
        params["tier"] = tier
    url = f"{gateway.rstrip('/')}/v1/events?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — operator-supplied gateway
        return json.load(resp)


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, name TEXT, category TEXT, city TEXT, region TEXT, venue TEXT,
            artists TEXT, starts_at TEXT, price_min REAL, currency TEXT, is_free INTEGER,
            fill_ratio REAL, image_url TEXT, source TEXT, source_url TEXT,
            redistribution_tier TEXT, updated_at TEXT
        )
        """
    )
    db.execute("CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()


def get_cursor(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM sync_state WHERE key='events_cursor'").fetchone()
    return row[0] if row else None


def set_cursor(db: sqlite3.Connection, value: str) -> None:
    db.execute(
        "INSERT INTO sync_state(key, value) VALUES('events_cursor', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (value,),
    )
    db.commit()


def upsert_event(db: sqlite3.Connection, e: dict) -> None:
    db.execute(
        """
        INSERT INTO events (id, name, category, city, region, venue, artists, starts_at,
            price_min, currency, is_free, fill_ratio, image_url, source, source_url,
            redistribution_tier, updated_at)
        VALUES (:id, :name, :category, :city, :region, :venue, :artists, :starts_at,
            :price_min, :currency, :is_free, :fill_ratio, :image_url, :source, :source_url,
            :redistribution_tier, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, category=excluded.category, city=excluded.city,
            region=excluded.region, venue=excluded.venue, artists=excluded.artists,
            starts_at=excluded.starts_at, price_min=excluded.price_min, currency=excluded.currency,
            is_free=excluded.is_free, fill_ratio=excluded.fill_ratio, image_url=excluded.image_url,
            source=excluded.source, source_url=excluded.source_url,
            redistribution_tier=excluded.redistribution_tier, updated_at=excluded.updated_at
        """,
        {**e, "artists": json.dumps(e.get("artists") or []), "is_free": int(bool(e.get("is_free")))},
    )


def sync(gateway: str, db_path: str, tier: str | None) -> None:
    db = sqlite3.connect(db_path)
    ensure_schema(db)
    cursor = get_cursor(db)
    print(f"syncing from {gateway} since {cursor or '(full backfill)'}")

    offset, synced, max_updated = 0, 0, cursor or ""
    while True:
        body = fetch_page(gateway, updated_since=cursor, tier=tier, offset=offset)
        events = body.get("events", [])
        for e in events:
            upsert_event(db, e)
            synced += 1
            if (e.get("updated_at") or "") > max_updated:
                max_updated = e["updated_at"]
        db.commit()
        offset += PAGE
        if offset >= body.get("count", 0) or not events:
            break

    if max_updated and max_updated != (cursor or ""):
        set_cursor(db, max_updated)
    tiers = dict(db.execute(
        "SELECT redistribution_tier, COUNT(*) FROM events GROUP BY redistribution_tier"
    ).fetchall())
    print(f"upserted {synced} events; total in db by tier: {tiers}; cursor -> {max_updated or '(unchanged)'}")
    db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync the n-quark events feed into a local DB.")
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--db", default="crawlspace_events.sqlite")
    ap.add_argument("--tier", default=None, help="open | link_only | excluded (default: all but excluded)")
    args = ap.parse_args()
    sync(args.gateway, args.db, args.tier)


if __name__ == "__main__":
    main()
