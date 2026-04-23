#!/usr/bin/env bash
# #532 — SlyLED community profile database backup.
#
# Runs on the electricrv.ca cPanel host via nightly cron. Dumps the
# community MySQL database into a timestamped, gzipped SQL file in a
# non-public directory, then prunes old archives on a rolling schedule
# (7 daily + 4 weekly-Sunday + 6 monthly-1st).
#
# Setup:
#   1. Put this script somewhere outside public_html, e.g.
#      /home/<cpuser>/bin/slyled-backup.sh
#   2. chmod 700 /home/<cpuser>/bin/slyled-backup.sh
#   3. Create ~/.slyled-backup.env (chmod 600) with:
#        DB_USER=...
#        DB_PASS=...
#        DB_NAME=slyled_community
#        BACKUP_DIR=/home/<cpuser>/backups/slyled
#   4. Add a cron job in cPanel → Cron Jobs:
#        0 3 * * *  /home/<cpuser>/bin/slyled-backup.sh >> /home/<cpuser>/logs/slyled-backup.log 2>&1
#
# Restore procedure is in docs/ops.md.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────
ENV_FILE="${ENV_FILE:-$HOME/.slyled-backup.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[$(date -Is)] ERROR: $ENV_FILE not found (chmod 600 it with DB creds)" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${DB_USER:?}"; : "${DB_PASS:?}"; : "${DB_NAME:?slyled_community}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/slyled}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y%m%d)"
DOW="$(date +%u)"        # 1=Mon..7=Sun
DOM="$(date +%d)"        # 01..31

DAILY_FILE="$BACKUP_DIR/daily-$STAMP.sql.gz"
WEEKLY_FILE="$BACKUP_DIR/weekly-$STAMP.sql.gz"
MONTHLY_FILE="$BACKUP_DIR/monthly-$STAMP.sql.gz"

# ── Dump ──────────────────────────────────────────────────────────────
echo "[$(date -Is)] slyled backup: dumping $DB_NAME → $DAILY_FILE"
mysqldump --single-transaction --quick --lock-tables=false \
          -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" \
  | gzip -9 > "$DAILY_FILE"
chmod 600 "$DAILY_FILE"

SIZE="$(stat -c%s "$DAILY_FILE" 2>/dev/null || wc -c <"$DAILY_FILE")"
echo "[$(date -Is)] slyled backup: daily OK ($SIZE bytes)"

# ── Weekly promotion (Sunday) ─────────────────────────────────────────
if [[ "$DOW" == "7" ]]; then
  cp -f "$DAILY_FILE" "$WEEKLY_FILE"
  chmod 600 "$WEEKLY_FILE"
  echo "[$(date -Is)] slyled backup: promoted to weekly"
fi

# ── Monthly promotion (1st of month) ──────────────────────────────────
if [[ "$DOM" == "01" ]]; then
  cp -f "$DAILY_FILE" "$MONTHLY_FILE"
  chmod 600 "$MONTHLY_FILE"
  echo "[$(date -Is)] slyled backup: promoted to monthly"
fi

# ── Retention ─────────────────────────────────────────────────────────
# Keep the 7 newest daily archives.
ls -1t "$BACKUP_DIR"/daily-*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
# Keep the 4 newest weekly archives.
ls -1t "$BACKUP_DIR"/weekly-*.sql.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
# Keep the 6 newest monthly archives.
ls -1t "$BACKUP_DIR"/monthly-*.sql.gz 2>/dev/null | tail -n +7 | xargs -r rm -f

REMAINING="$(find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' | wc -l)"
echo "[$(date -Is)] slyled backup: done — $REMAINING archive(s) retained"
