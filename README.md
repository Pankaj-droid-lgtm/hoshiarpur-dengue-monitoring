# Hoshiarpur Dengue Field Surveillance and Monitoring System

## Runtime and local setup

The project targets Python 3.11. Create an isolated virtual environment before
installing dependencies; do not rely on globally installed Python packages.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set a unique secret before starting the application. The local SQLite database
is created only when the initialization command is run.

```powershell
$env:DENGUE_SECRET_KEY = "replace-with-a-long-random-secret"
flask --app app:create_app init-db
flask --app app:create_app run
```

Before first use, create an administrator interactively. This command does not
create a worker record or expose a default password:

```powershell
flask --app app:create_app create-admin
```

`DENGUE_DATABASE_URL` optionally overrides the default SQLite database at
`instance/dengue.db`. Do not use the development database for shared or
production deployment.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DENGUE_SECRET_KEY` | Yes | A long, unique secret used to sign sessions. |
| `DENGUE_DATABASE_URL` | Production | SQLAlchemy database URL. SQLite remains the local default. |
| `DENGUE_UPLOAD_DIRECTORY` | Production | Private directory for visit photos. Defaults to `instance/uploads`. |
| `DENGUE_SESSION_COOKIE_SECURE` | Production | Set to `true` when the application is served through HTTPS. |
| `DENGUE_HOST` | No | Gunicorn bind host, default `0.0.0.0`. |
| `DENGUE_PORT` | No | Gunicorn bind port, default `8000`. |

Keep these values in the cloud platform's secret/environment configuration.
Never commit them to the repository.

## Production startup

The production WSGI entry point is [wsgi.py](wsgi.py). After setting the
required environment variables and installing the pinned dependencies, create
the empty database deliberately:

```sh
flask --app app:create_app init-db
flask --app app:create_app create-admin
gunicorn --bind "${DENGUE_HOST:-0.0.0.0}:${DENGUE_PORT:-8000}" wsgi:app
```

Use a managed database URL for shared/cloud deployments. The application does
not automatically import Excel workbooks or create operational records.

The unauthenticated health endpoint is `GET /health` and returns only
`{"status":"ok"}`.

## Uploads and historical imports

Visit photos are private application data. Configure `DENGUE_UPLOAD_DIRECTORY`
outside the repository and provide a persistent writable location in the cloud.
Photo access requires authentication and deployment authorization.

Historical reporting workbooks are imported manually from **Historical import**
by an authorized user. The importer accepts `.xlsx` files, stores the source
file name and SHA-256 digest, and preserves source-row values separately from
operational data. It does not create houses, workers, deployments, visits,
coordinates, or reporting dates from filename text.

## Dependency policy

Runtime packages are pinned in `requirements.txt` so that the application can
be recreated with the same dependency versions. Dependencies must only be
added when the existing stack cannot reliably meet a requirement. Use mature,
actively maintained open-source packages and avoid services requiring trial
accounts, paid APIs, temporary credentials, or beta releases.

Do not run automatic dependency upgrades. Review and test each proposed
version change before updating `requirements.txt`.

## Configuration

Application configuration must be supplied through environment variables or a
local, untracked `.env` file. Keep secrets, API keys, passwords, and
deployment-specific settings out of the repository and frontend code.

External integrations, if ever needed, must be isolated behind an application
boundary so the core monitoring workflow remains usable when the integration
is unavailable.

## Development standards

Keep implementations focused and readable. Avoid unused code, duplicate
business logic, unnecessary database queries, and dependencies with no direct
purpose. Comments should document non-obvious reasoning, security-sensitive
behaviour, or deployment decisions in concise professional English.
