# Tresorapide

Bootstrap foundation for a local-network-oriented housing co-op treasurer web application.

> This deployment path is for a trusted local network only. It intentionally keeps the stack small by serving the app, static files, and media directly from Django inside Docker Compose. Do not expose it directly to the public internet.

## Current foundation

- Django + Django REST Framework
- PostgreSQL-ready database settings
- Docker Compose stack for Django + PostgreSQL
- Plain Django templates and forms
- Local filesystem static/media storage
- Custom user model with baseline `admin`, `treasurer`, and `viewer` roles
- Reusable role-threshold helpers for future Django and DRF authorization
- Read-only dashboard, budget transparency, reimbursement archive, and audit browsing pages
- Minimal app boundaries for `core`, `accounts`, `members`, `budget`, `reimbursements`, and `audits`

## Quick start on Windows (recommended)

For the supported Windows local-network deployment, double-click `Start Tresorapide.cmd` from the repository root.

You can also run it from PowerShell:

```powershell
& '.\Start Tresorapide.cmd'
```

### First run

On the first run, the launcher:

- checks that Docker Desktop and Docker Compose are available
- tries to start Docker Desktop if it is installed but not running yet
- creates or repairs `.env` from `.env.example`
- asks for the LAN hostname/IP and ports it should use
- generates secure values such as `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` when needed
- validates `docker-compose.yml`, starts the stack, and waits for `http://localhost:<port>/api/ready/`
- offers to run `createsuperuser` if no users exist yet
- opens the app in your default browser unless you disable that

### Later runs

On later runs, the launcher usually reuses the existing `.env`, starts or updates the Docker Compose stack, waits for readiness, skips the initial superuser prompt once users exist, and reopens the app.

### Useful launcher options

`Start Tresorapide.cmd` forwards arguments to `scripts\start_tresorapide.ps1`.

```powershell
& '.\Start Tresorapide.cmd' -DryRun
& '.\Start Tresorapide.cmd' -NonInteractive -AcceptDefaults -NoBrowser
```

- `-DryRun`: preview `.env` preparation and Compose validation without writing files or starting containers
- `-NonInteractive`: do not prompt for input; if setup is still required, use it together with `-AcceptDefaults`
- `-AcceptDefaults`: automatically accept the launcher's detected or default setup values
- `-NoBrowser`: leave the browser closed after startup

### Development reset helper

To wipe development transaction data while keeping houses, members, budget years, and sub-budgets, use the root helper next to `Start Tresorapide.cmd`:

```powershell
& '.\Reset Test Data.cmd'
& '.\Reset Test Data.cmd' -Yes
```

This runs Django's `reset_test_data` command inside Docker Compose and removes expenses, bons de commande, OCR data, uploaded receipt files, merchants, and audit entries.

### Coop house directory import

To import or refresh the cooperative house directory from the reference PDF table checked into `infos_coop/`, run:

```powershell
docker compose exec web python manage.py import_coop_houses
```

Use `--dry-run` if you only want to preview the changes.

### Common recovery notes

- **Docker Desktop is not installed:** install Docker Desktop, then rerun `Start Tresorapide.cmd`.
- **Docker Desktop is installed but not running:** start Docker Desktop and wait for the engine to report as running, then rerun the launcher. The launcher also tries to start it automatically when it can.
- **You need a fresh `.env`:** rename or delete `.env`, then rerun `Start Tresorapide.cmd`. The launcher will recreate it from `.env.example`. If only the network values are incomplete, the launcher can usually repair them.
- **You need the app URL:** open `http://localhost:8000/` by default, or use the port you chose during setup. The readiness URL is `http://localhost:<port>/api/ready/`.

## Manual Docker Compose (secondary)

Use this only if you want to manage the supported Docker Compose stack yourself instead of using the Windows launcher above.

### 1. Prepare the environment

```powershell
Copy-Item .env.example .env
```

Before you start the stack, edit `.env` and replace:

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `192.168.1.50` with the actual LAN IP address or hostname of the machine running Docker
- `APP_PUBLISHED_PORT` or `POSTGRES_PUBLISHED_PORT` only if `8000` or `5432` are already in use on the host

### 2. Start the stack

```powershell
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Open:

- `http://localhost:<published-port>/` from the Docker host (`8000` by default)
- `http://<your-lan-ip>:<published-port>/` from another device on the same network
- `http://localhost:<published-port>/api/ready/` for the readiness check

Follow logs when needed:

```powershell
docker compose logs -f web
```

### 3. Persisted data

The Compose stack keeps its data in named volumes:

- `postgres_data`: PostgreSQL data directory
- `media_data`: uploaded files and any future attachment storage
- `static_data`: collected static assets for the running container

`docker compose down` stops containers but keeps those volumes.

`docker compose down -v` deletes all persisted application data and should only be used when you intentionally want a full reset.

PostgreSQL is published to `127.0.0.1` on the configured host port, so database access stays on the Docker host instead of the wider LAN.

### 4. Backups

Back up three things together:

1. `.env` for secrets and host-specific settings
2. `database.sql` from PostgreSQL
3. `media.tar.gz` from the media volume

Static files do not need to be backed up because `collectstatic` rebuilds them during startup and restore.

The repo now includes a backup helper that creates timestamped folders under `.\backups\`:

```powershell
python .\scripts\compose_backup.py
```

Each backup folder contains:

- `database.sql`
- `media.tar.gz`
- `.env.backup` when `.env` exists locally

Suggested convention: keep at least one recent daily backup and one recent pre-upgrade backup before schema or deployment changes.

### 5. Restore and verify

Restore from a backup folder with:

```powershell
python .\scripts\compose_restore.py .\backups\YYYYMMDD-HHMMSS-ffffff
```

The restore helper will:

- bring up PostgreSQL
- restore `database.sql`
- bring up the web container
- restore `media.tar.gz`
- rerun `collectstatic`
- run `python manage.py check`
- call `/api/ready/`
- print a quick `users=<count> media_files=<count>` summary

After the scripted restore, manually verify:

1. the home page loads from another device on the LAN
2. you can sign in with an expected account
3. `/api/ready/` returns `{"status":"ok",...}`
4. at least one expected uploaded file is present once the app starts using media storage

### 6. Safe shutdown

```powershell
docker compose down
```

Leave off `-v` unless you intentionally want to destroy PostgreSQL, media, and collected static volumes.

## Optional local Python checks

When you want to validate the foundation quickly without PostgreSQL, leave `.env` absent and Django will use SQLite for local checks and tests.

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux shell

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Leave `.env` absent when you want SQLite-backed local checks. Copy `.env.example` only when you are running through Docker Compose or pointing Django at a reachable PostgreSQL instance.

## Local development commands

```powershell
.\venv\Scripts\python manage.py migrate
.\venv\Scripts\python manage.py createsuperuser
.\venv\Scripts\python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Validation commands

```powershell
.\venv\Scripts\python manage.py check
.\venv\Scripts\python manage.py makemigrations --check --dry-run
.\venv\Scripts\python manage.py test
docker compose config
```

## App boundaries

- `core`: landing page, foundational routes, health endpoint
- `accounts`: custom user model, auth wiring, and reusable role-aware access helpers
- `members`: future co-op member records and relationships
- `budget`: future budgeting and forecasting workflows
- `reimbursements`: future expense and reimbursement workflows
- `audits`: future audit trail and review workflows
