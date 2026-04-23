# SlyLED — Operations Runbook

## Community profile database backups (#532)

The community profile database lives on the electricrv.ca cPanel MySQL
server (`slyled_community`). A nightly cron job snapshots it into
timestamped `.sql.gz` archives; this section documents the setup and
the restore procedure.

### What's in place

- **Script:** `server/backup.sh` (shipped in the repo; deploy via SCP).
- **Schedule:** nightly at 03:00 server time via cPanel Cron Jobs.
- **Retention (rolling):**
  - 7 daily archives (one per night for the last week)
  - 4 weekly archives (one per Sunday-night dump for the last month)
  - 6 monthly archives (one per first-of-month dump for the last ~6 months)
- **Location:** `$HOME/backups/slyled/` (outside `public_html`), mode
  700 directory, 600 files, owned by the cPanel user.

### Initial setup

On the cPanel host:

```bash
# 1. Drop the backup script somewhere outside public_html.
mkdir -p ~/bin
scp ./server/backup.sh cpuser@electricrv.ca:~/bin/slyled-backup.sh
ssh cpuser@electricrv.ca 'chmod 700 ~/bin/slyled-backup.sh'

# 2. Create the credentials file (chmod 600, never checked in).
ssh cpuser@electricrv.ca 'cat > ~/.slyled-backup.env <<EOF
DB_USER=slyled_community_user
DB_PASS=<the actual mysql password>
DB_NAME=slyled_community
BACKUP_DIR=/home/cpuser/backups/slyled
EOF
chmod 600 ~/.slyled-backup.env'

# 3. Add the cron job in cPanel → Cron Jobs:
#     0 3 * * *  $HOME/bin/slyled-backup.sh >> $HOME/logs/slyled-backup.log 2>&1
```

### Verifying a backup

```bash
ssh cpuser@electricrv.ca 'ls -la ~/backups/slyled/'
# → daily-20260423.sql.gz  weekly-20260420.sql.gz  ...
ssh cpuser@electricrv.ca 'zcat ~/backups/slyled/daily-*.sql.gz | head -30'
# → DROP TABLE IF EXISTS ...; CREATE TABLE profiles (...
```

### Restore procedure

If a bad overwrite corrupts a profile or the table is truncated
accidentally:

```bash
# 1. Copy the most recent good archive off the server (local working
#    copy, don't disturb the archives).
scp cpuser@electricrv.ca:~/backups/slyled/daily-YYYYMMDD.sql.gz /tmp/

# 2. Restore either the whole database (nuclear — wipes current state):
zcat /tmp/daily-YYYYMMDD.sql.gz \
  | mysql -u slyled_community_user -p slyled_community

# 3. OR restore a single profile by row (targeted, doesn't touch other rows):
zcat /tmp/daily-YYYYMMDD.sql.gz \
  | grep "INSERT INTO \`profiles\` .* 'the-slug-you-want'" \
  | mysql -u slyled_community_user -p slyled_community
# (inspect the extracted INSERT before running; turn it into an
# UPDATE if the target row still exists.)
```

### Off-host copy (optional, strongly recommended)

A full cPanel compromise would erase every backup that lives on the
same account. Mirror the latest weekly archive to a different host (or
a consumer cloud drive) via a second cron job:

```bash
0 4 * * 0  rsync -az ~/backups/slyled/weekly-$(date +\%Y\%m\%d).sql.gz \
             offsite-user@backup-host:/mnt/backup/slyled/
```

Run this as a separate 04:00 Sunday cron so it fires one hour after
the promotion step in `slyled-backup.sh`. Authenticate with a
dedicated SSH key that only has write access to the backup directory
(restrict via `command=` in the remote `authorized_keys`).

### Monitoring

The backup log lives at `~/logs/slyled-backup.log` on cPanel. Tail it
to confirm cron runs (it'll append one line per night whether things
worked or not):

```bash
tail -30 ~/logs/slyled-backup.log
```

If the archive size drops by more than 50 % between consecutive nights
without an obvious cause, investigate — it usually means rows got
deleted. Fast-response restores from the previous daily are cheap.
