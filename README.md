# Hoshiarpur Dengue Field Surveillance and Monitoring System

## Supported runtime

The project targets Python 3.11. Create an isolated virtual environment before
installing dependencies; do not rely on globally installed Python packages.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set a unique secret before starting the application:

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
