# Hoshiarpur Dengue Monitoring System — Delivery Guide

## Overview and architecture

The Flask application supports the operational flow: permanent `Worker` → daily
`Deployment` → `HouseAssignment` → `HouseVisit`. Workers and houses use stable,
unique identifiers (`official_worker_id` and `house_code`). Historical Excel
imports remain separate from operational field data.

Production runs Gunicorn through the `hoshiarpur-dengue-monitoring` systemd
service. Its application entry point is `wsgi:app`; the service reads its
environment from `/etc/hoshiarpur-dengue-monitoring.env`.

## Roles

- **Admin and operational management roles:** manage workers, accounts,
  geography, daily deployments, house assignments, and eligible reassignments.
- **ADC:** monitoring-only access to dashboards, deployment progress, reports,
  map, and historical monitoring. ADC cannot make operational changes.
- **Field worker:** access only to deployments linked to their own permanent
  worker record, assigned houses, their visits, and authorized visit photos.

## Operational workflows

### Admin

1. Create or update a permanent worker with a unique official Worker ID.
2. Create one linked worker account when login access is required; the suggested
   username is the official Worker ID.
3. Maintain approved blocks, localities, and permanent houses.
4. Create a daily deployment by selecting an active permanent worker, date,
   block, and optional locality. Worker identity/account/contact/designation/
   supervisor values are retained as deployment snapshots.
5. Search stable house ID or address and assign eligible existing houses.
6. Reassign only pending houses to another active same-date deployment. Completed
   or visited houses are not reassigned.

### ADC

Use **Monitoring**, **Deployment progress**, **Map**, and CSV reports for
district-wide oversight. The ADC role has no worker, geography, import, or
assignment write permission.

### Field worker

The field worker signs in with the account linked to their Worker record and
uses **My field work**. The worker sees only today's assigned block/locality and
houses, records visits, optional valid GPS and photos, larval observations,
source reduction, larvicide, and remarks. When every assigned house is
completed, the deployment becomes completed automatically.

## Worker accounts and credential handover

Passwords are hashed by the server and are never stored or displayed as plain
text. An administrator creates or resets a worker password through the worker
account screen. Give passwords to account holders through an approved separate
channel. Use [DELIVERY_CREDENTIALS_TEMPLATE.md](DELIVERY_CREDENTIALS_TEMPLATE.md)
for the non-secret handover record; never commit completed copies.

## Authoritative operational-data CSV format

No real government roster, geography, or house data is included in this
repository. Before any import is implemented or run, prepare reviewed CSV files
using these headers and retain the approved original as the authority.

`workers.csv`

```text
official_worker_id,full_name,designation,phone_number,supervisor_official_worker_id,availability_status,requires_login
```

`blocks.csv`

```text
block_name,is_urban
```

`localities.csv`

```text
block_name,locality_name,locality_type
```

`houses.csv`

```text
house_code,block_name,locality_name,house_number,address,latitude,longitude,is_active
```

Rules: IDs must be unique and stable; workers must be imported before their
supervisor references; blocks before localities; localities before houses.
Blank phone, GPS, supervisor, and login fields remain blank rather than being
invented. Passwords must never appear in CSV files. Validate duplicates,
foreign keys, and data ownership in a staging copy before any production import.
There is intentionally no automatic seed/import command for operational data.

## Database and backups

The production SQLite database is `instance/dengue.db`. Normal application
startup does not create, reset, or migrate it. Do **not** run `init-db` on an
existing production installation.

Create a consistent backup while the application is running:

```sh
cd ~/hoshiarpur-dengue-monitoring
mkdir -p instance/backups
BACKUP_NAME="instance/backups/dengue.db.backup-$(date +%Y%m%d-%H%M%S)"
BACKUP_NAME="$BACKUP_NAME" .venv/bin/python - <<'PY'
import os
import sqlite3

source = sqlite3.connect("instance/dengue.db")
destination = sqlite3.connect(os.environ["BACKUP_NAME"])
source.backup(destination)
destination.close()
source.close()
PY
chmod 600 "$BACKUP_NAME"
```

To restore, first stop the service, preserve the current database under a new
backup name, copy a verified backup into place, set ownership, and restart:

```sh
sudo systemctl stop hoshiarpur-dengue-monitoring
cp instance/dengue.db "instance/backups/dengue.db.before-restore-$(date +%Y%m%d-%H%M%S)"
cp /absolute/path/to/verified/dengue.db.backup-YYYYMMDD-HHMMSS instance/dengue.db
chown ec2-user:ec2-user instance/dengue.db
chmod 600 instance/dengue.db
sudo systemctl start hoshiarpur-dengue-monitoring
```

Do not commit databases, backups, uploads, or secret files.

## AWS service management and security

```sh
sudo systemctl status hoshiarpur-dengue-monitoring
sudo systemctl restart hoshiarpur-dengue-monitoring
curl http://127.0.0.1:8000/health
```

`DENGUE_SECRET_KEY` is mandatory and read only from the server environment.
`ProductionConfig` disables debug mode and enables secure session cookies by
default. Visit uploads are private and authenticated; they must not be stored in
a public static directory. See `README.md` for protected environment-file and
systemd setup.

## Known limitations and next steps

- The repository does not contain authoritative worker, geography, house, or
  credential data. Load only reviewed official source data.
- Operational CSV import is deliberately documented rather than automated until
  the authoritative data owners approve the files and import rules.
- Existing historical spreadsheet imports are reporting-only and do not create
  operational workers, houses, deployments, or visits.
