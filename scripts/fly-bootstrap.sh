#!/usr/bin/env bash
# Phase 4D — one-time collection bootstrap for an empty cloud DB.
# Runs a bounded Boshow/District discovery+enrollment and one capture pass through the existing
# scheduler, inside the crawl machine (which reaches signal/graph/media over Flycast). Idempotent —
# safe to re-run (no duplicate tracked events). Never enables Skillbox. Set DRY_RUN=1 to print only.
set -euo pipefail

CRAWL_APP="${CRAWL_APP:-nquark-crawl-service}"
run() { echo "+ $*"; [ "${DRY_RUN:-0}" = "1" ] || "$@"; }

command -v flyctl >/dev/null 2>&1 || { echo "flyctl not found — install it and 'fly auth login' first." >&2; exit 1; }

echo "== bootstrapping collection on ${CRAWL_APP} (Boshow + District; Skillbox excluded) =="
run flyctl ssh console --app "${CRAWL_APP}" -C "python -m crawl_service.bootstrap"
echo "Bootstrap complete. Re-running is safe (idempotent enrollment)."
