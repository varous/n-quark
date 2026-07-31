#!/usr/bin/env python3
"""Scheduled ingest job — runs on Fly as a cron machine, then exits.

Walks the ticketing provider's discovery list and ingests each event through signal-service,
which fans out to observation -> entity -> graph. Idempotent: re-ingesting MERGEs, so running
this daily just refreshes the catalog (fill_ratio, price, new events) without duplicating.

Targets signal-service directly over Fly's private network (flycast) — set NQUARK_INGEST_TARGET.
Stdlib only, so the cron image stays tiny.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TARGET = os.environ.get("NQUARK_INGEST_TARGET", "http://nquark-signal-service.flycast").rstrip("/")
CITY = os.environ.get("NQUARK_INGEST_CITY") or None
LIMIT = int(os.environ.get("NQUARK_INGEST_LIMIT", "50"))
TIMEOUT = int(os.environ.get("NQUARK_INGEST_TIMEOUT", "60"))


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:  # noqa: S310 — operator-set target
        return json.load(resp)


def _post(url: str) -> dict:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return json.load(resp)


def main() -> int:
    params = {"limit": LIMIT}
    if CITY:
        params["city"] = CITY
    discover_url = f"{TARGET}/v1/signals/ticketing/discover?" + urllib.parse.urlencode(params)
    print(f"[ingest-cron] discovering: {discover_url}", flush=True)

    try:
        body = _get(discover_url)
    except urllib.error.URLError as exc:
        print(f"[ingest-cron] discovery FAILED: {exc}", file=sys.stderr, flush=True)
        return 1

    refs = body.get("event_refs", [])
    print(f"[ingest-cron] provider={body.get('provider')} refs={len(refs)}", flush=True)

    ingested = failed = 0
    for ref in refs:
        url = f"{TARGET}/v1/signals/ticketing/events/{urllib.parse.quote(ref)}/ingest"
        try:
            result = _post(url)
            g = result.get("graph") or {}
            print(f"[ingest-cron] ok {ref} -> graph nodes={g.get('nodes')} edges={g.get('edges')}", flush=True)
            ingested += 1
        except urllib.error.URLError as exc:  # keep going; one bad event shouldn't fail the run
            print(f"[ingest-cron] FAIL {ref}: {exc}", file=sys.stderr, flush=True)
            failed += 1

    print(f"[ingest-cron] done: ingested={ingested} failed={failed}", flush=True)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
