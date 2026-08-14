# Silicon Dominoes — Collection Layer (CT 109 `dominoes`)
Build-order step 3. Runs on the KAMRUI Proxmox host per homelab principle: Mac = inference only; orchestration lives on Proxmox. Postgres runs natively in the CT (no Docker-in-LXC); raw archive lives on the Synology over NFS; alerts via ntfy; scheduling via systemd timers.

Every step ends with a verification command. Paste output back if anything looks off.

## 0. Before touching Proxmox: Synology NFS allow-list
The known gotcha: NFS exports are IP-allow-listed, and the mount will fail cryptically if the CT's IP isn't added FIRST.

On the Synology (DSM → Control Panel → Shared Folder): create a shared folder `silicon-dominoes` (or reuse an existing data share), then Edit → NFS Permissions → Create: hostname/IP `192.168.1.204`, privilege Read/Write, squash "Map all users to admin" (matches your existing pattern), and note the mount path shown at the bottom (e.g. `/volume1/silicon-dominoes`).

## 1. Create CT 109
On the Proxmox host (`192.168.1.50`), adjust the template filename to whichever Debian 13 template you already have in local storage:

```
pct create 109 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname dominoes --cores 2 --memory 3072 --swap 512 \
  --rootfs local-lvm:12 --unprivileged 1 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.204/24,gw=192.168.1.1 \
  --features nesting=1 \
  --onboot 1
pct start 109
```

Verify:
```
pct status 109
pct exec 109 -- ip -4 addr show eth0
```
Expect `status: running` and `192.168.1.204`.

## 2. Base packages
```
pct enter 109
```
(You land as root.) Then:
```
apt update && apt install -y postgresql postgresql-client python3-venv python3-pip git nfs-common curl
```

Verify:
```
pg_lsclusters
```
Expect one cluster, `online`, port 5432.

## 3. Mount the NAS archive
```
mkdir -p /mnt/nas-archive
echo '192.168.1.163:/volume1/silicon-dominoes /mnt/nas-archive nfs vers=3,nofail,x-systemd.automount 0 0' >> /etc/fstab
systemctl daemon-reload
mount /mnt/nas-archive
```

Verify (write test):
```
touch /mnt/nas-archive/write-test && ls -la /mnt/nas-archive/ && rm /mnt/nas-archive/write-test
```
If mount fails with "access denied": the allow-list from step 0 is missing `.204` — fix there, not here.

## 4. Service user and database
Peer authentication over the unix socket means no database password exists anywhere — nothing to leak into terminal output or env files.

```
useradd -m -s /bin/bash dominoes
su - postgres -c "createuser dominoes"
su - postgres -c "createdb -O dominoes silicon_dominoes"
```

Apply the schema (from the repo; clone it first):
```
mkdir -p /opt/silicon-dominoes && cd /opt/silicon-dominoes
git clone https://github.com/bjharbison/silicon-dominoes.git .
su - postgres -c "psql -d silicon_dominoes -v ON_ERROR_STOP=1 -f /opt/silicon-dominoes/db/schema.sql"
su - postgres -c "psql -d silicon_dominoes -v ON_ERROR_STOP=1 -f /opt/silicon-dominoes/collection/sql/002_url_snapshots.sql"
su - postgres -c "psql -d silicon_dominoes -c 'GRANT sd_pipeline TO dominoes'"
su - postgres -c "psql -d silicon_dominoes -c 'GRANT USAGE, CREATE ON SCHEMA public TO dominoes'"
```

Run the step-2 guarantee tests now that there's finally a live Postgres:
```
su - postgres -c "psql -d silicon_dominoes -v ON_ERROR_STOP=1 -f /opt/silicon-dominoes/db/test_schema.sql"
```
Expect a series of `NOTICE: PASS T1..T11` lines ending in "All schema guarantee tests passed." If any test fails, stop and report the output.

Verify peer auth works for the service user:
```
su - dominoes -c "psql -d silicon_dominoes -c 'SELECT count(*) FROM feeds;'"
```
Expect `0` (empty table, no password prompt).

## 5. Python environment
```
cd /opt/silicon-dominoes/collection
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chown -R dominoes:dominoes /opt/silicon-dominoes
```

Verify:
```
su - dominoes -c "cd /opt/silicon-dominoes/collection && .venv/bin/python -c 'import feedparser, psycopg, yaml, requests; print(\"imports ok\")'"
```

## 6. Environment file
```
mkdir -p /etc/silicon-dominoes
cat > /etc/silicon-dominoes/collector.env << 'ENVEOF'
SD_DB_URL=postgresql:///silicon_dominoes
SD_ARCHIVE_DIR=/mnt/nas-archive/raw-archive
SD_NTFY_URL=
SD_WAYBACK=1
ENVEOF
mkdir -p /mnt/nas-archive/raw-archive && chown dominoes:dominoes /mnt/nas-archive/raw-archive
```
Set `SD_NTFY_URL` to your ntfy topic (self-hosted or ntfy.sh) when ready — empty means alerts print to the journal only. No secrets go in this file; the DB uses peer auth.

## 7. First manual run
```
su - dominoes
cd /opt/silicon-dominoes/collection
set -a; source /etc/silicon-dominoes/collector.env; set +a
.venv/bin/python -m collector.poll_rss
```
Expect per-feed lines like `[rss-techwireasia] entries=20 new=20`. Some feeds WILL error — several URLs in feeds.yaml are marked `unverified` and finding the dead ones on day one is the plan, not a problem. Fix a URL by editing feeds.yaml and rerunning.

Verify captures landed in both the database and the NAS:
```
psql -d silicon_dominoes -c "SELECT feed_id, count(*) FROM raw_captures GROUP BY 1 ORDER BY 2 DESC;"
find /mnt/nas-archive/raw-archive/raw -type f | head -5
exit
```

## 8. Install the timers
Back as root in the CT:
```
cp /opt/silicon-dominoes/collection/systemd/*.service /opt/silicon-dominoes/collection/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sd-rss.timer sd-gdelt.timer sd-verify.timer sd-snapshot-retry.timer sd-health.timer
```

Verify:
```
SYSTEMD_PAGER=cat systemctl list-timers 'sd-*'
```
Expect five timers with NEXT times. Watch a run live later with:
```
journalctl -u sd-rss.service --since "today" --no-pager
```
(Remember: `journalctl -b` inside LXC shows the host boot — use `--since`.)

## 9. Backups
- CT 109 → add to your existing vzdump job to the Synology (covers Postgres).
- The raw archive on the NAS is THE asset to protect hardest (archive + approved events regenerate every score). Offsite copy is a required TODO, not optional: simplest is `rclone` from the NAS or CT to Backblaze B2 on a daily timer. Flagging it here so it doesn't silently drop off.

## 10. What runs when
| Timer | Cadence | Does |
|---|---|---|
| sd-rss | every 2h | polls RSS feeds, archives new articles, Wayback-snapshots them |
| sd-gdelt | every 6h | runs the faceted GDELT query, archives the result set |
| sd-verify | daily 07:10 | fetches WAICO / Pax Silica pages, alerts on change or failure |
| sd-snapshot-retry | every 6h | Wayback-retries captures that missed a snapshot |
| sd-health | daily 08:05 | capture-rate baselines, staleness rules, opens research_gaps, ntfy |

## Known open items
- WAICO / Pax Silica URLs in feeds.yaml are placeholders (`REPLACE-ME`) — they will alert as failing until you identify the canonical membership pages, which is the staleness rule working as intended.
- Several pilot RSS URLs are `unverified` — expect to tune feeds.yaml in week one.
- ArchiveBox (preferred self-hosted snapshotter, ARCHITECTURE.md §3) can be added later in this CT or on the NAS; Wayback covers the guarantee meanwhile.
- Offsite archive backup (step 9) — decide B2 vs private GitHub repo.
