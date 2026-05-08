# NexusLeague Exporter

The web app + data layer for the NexusLeague platform. A FastAPI server that:

- Hosts the **web dashboard** (Jinja2 templates) for league owners.
- Receives **Madden Companion App** exports and stores them in PostgreSQL.
- Serves the same database that the [nexus-league-bot](https://github.com/Sevorcer/nexus-league-bot) Discord bot reads from.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Quick start (Docker)](#2-quick-start-docker)
3. [Quick start (local Python venv)](#3-quick-start-local-python-venv)
4. [Configure Discord OAuth](#4-configure-discord-oauth)
5. [Configure Madden Companion App](#5-configure-madden-companion-app)
6. [Daily workflow](#6-daily-workflow)
7. [Deploying to production](#7-deploying-to-production)
8. [Troubleshooting](#8-troubleshooting)
9. [Project layout](#9-project-layout)

---

## 1. Prerequisites

You only need **one** of:

| Path             | Install                                                                          |
| ---------------- | -------------------------------------------------------------------------------- |
| **Docker**       | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (recommended). |
| **Local Python** | Python 3.11+, PostgreSQL 14+ running on `localhost:5432`.                        |

You will also need a **Discord application** (free) for OAuth login — see [section 4](#4-configure-discord-oauth).

---

## 2. Quick start (Docker)

This is the simplest path. It spins up Postgres + the web app in two containers.

```bash
git clone https://github.com/Sevorcer/nexusexporter-clean.git
cd nexusexporter-clean

# 1. Copy env template and edit it.
cp .env.example .env
#    Required edits:
#      SECRET_KEY              -> long random string
#      DISCORD_CLIENT_ID       -> from your Discord app
#      DISCORD_CLIENT_SECRET   -> from your Discord app
#      DISCORD_REDIRECT_URI    -> http://localhost:8000/oauth-callback

# 2. Build and start everything.
docker compose up -d --build

# 3. Initialise the database (one-time).
docker compose exec web python -m scripts.init_db
```

Open <http://localhost:8000> — you should see the landing page. Click **Login with Discord**.

To stop: `docker compose down`. To wipe data: `docker compose down -v`.

---

## 3. Quick start (local Python venv)

Use this path if you don't want Docker.

### Windows (PowerShell)

```powershell
git clone https://github.com/Sevorcer/nexusexporter-clean.git
cd nexusexporter-clean

# One-shot helper: creates venv, installs deps, copies .env, initialises DB.
.\scripts\setup.ps1

# After editing .env, run again to bootstrap the DB:
.\scripts\setup.ps1

# Start the server:
.\.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

### macOS / Linux / WSL / Git Bash

```bash
git clone https://github.com/Sevorcer/nexusexporter-clean.git
cd nexusexporter-clean

python -m venv .venv
source .venv/bin/activate

make install     # pip install -r requirements.txt
make env         # cp .env.example .env  (edit it now)
make init-db     # python -m scripts.init_db
make dev         # uvicorn main:app --reload
```

Either way, the app should now be live at <http://localhost:8000>.

---

## 4. Configure Discord OAuth

Login is required to use the dashboard. Setup steps:

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. In the left sidebar pick **OAuth2 → General**.
3. Copy the **Client ID** → paste into `.env` as `DISCORD_CLIENT_ID`.
4. Click **Reset Secret**, copy the **Client Secret** → paste as `DISCORD_CLIENT_SECRET`.
5. Under **Redirects**, add **exactly** the URL in your `.env`:

   - Local: `http://localhost:8000/oauth-callback`
   - Production: `https://your-domain.example/oauth-callback`

6. Save changes in Discord.
7. Restart the app so the new env vars take effect.

> Each environment (local, staging, prod) needs its own redirect URI added in Discord. The URI in `.env` must match a redirect registered in Discord exactly — including scheme and trailing path.

---

## 5. Configure Madden Companion App

Once the dashboard is running and you have logged in:

1. From the dashboard, click **Create League** and give it a name.
2. Copy the generated **API key** for that league (the dashboard shows it once).
3. In the Madden Companion app, set the **export URL** to:

   ```
   https://YOUR-DOMAIN/api/league/<LEAGUE_ID>/<API_KEY>
   ```

4. Trigger an export from the Companion app. Roster, schedule, standings and stats will appear in the dashboard within seconds.

The Madden league ID can be set on the dashboard under **Set Madden League ID** so the Discord bot can disambiguate when one user owns multiple leagues.

---

## 6. Daily workflow

| Task                     | Command                                       |
| ------------------------ | --------------------------------------------- |
| Start dev server         | `make dev` / `uvicorn main:app --reload`      |
| Start Docker stack       | `docker compose up -d`                        |
| Stop Docker stack        | `docker compose down`                         |
| View web logs            | `make logs` / `docker compose logs -f web`    |
| Open Postgres shell      | `docker compose exec db psql -U nexus nexus`  |
| Wipe & recreate DB       | `make reset-db` / `python -m scripts.init_db --reset` |
| Run tests                | `python -m pytest` (when test suite is added) |

---

## 7. Deploying to production

The repo includes a production-ready `Dockerfile`. Common deployment targets:

### Railway

1. Create a Railway project, add a PostgreSQL plugin.
2. Add a service from this GitHub repo.
3. Set environment variables (copy from your local `.env`, but use Railway's Postgres `DATABASE_URL`).
4. Set `DISCORD_REDIRECT_URI` to `https://YOUR-RAILWAY-DOMAIN/oauth-callback` and add it to the Discord app.
5. Add the same redirect URI to Discord (section 4, step 5).
6. Deploy. After first deploy, run `python -m scripts.init_db` once via the Railway shell.

### Generic VPS / Docker host

```bash
docker compose -f docker-compose.yml up -d --build
docker compose exec web python -m scripts.init_db
```

Put a TLS-terminating reverse proxy (Caddy, Nginx, Traefik) in front and point it at port 8000.

---

## 8. Troubleshooting

| Symptom                                                         | Cause / Fix                                                                                                                        |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| App crashes at startup with `KeyError: 'DISCORD_CLIENT_ID'`     | `.env` not loaded. Make sure you copied `.env.example` to `.env` and that the file is in the project root.                         |
| `redirect_uri_mismatch` after Discord login                     | The `DISCORD_REDIRECT_URI` in `.env` does not match a redirect registered in the Discord developer portal. Must match exactly.     |
| `psycopg2.OperationalError: could not connect`                  | Postgres is not running, or `DATABASE_URL` is wrong. With Docker, run `docker compose ps` to confirm the `db` container is healthy.|
| Tables don't exist (`relation "league" does not exist`)         | Run `python -m scripts.init_db`.                                                                                                   |
| Companion App returns "Invalid API key"                         | The API key in the export URL must match the one shown when the league was created. Regenerate via the dashboard if needed.        |
| Dashboard styles look broken                                    | Hard-refresh (Ctrl+F5). Static files are served by FastAPI; if you changed `static/`, restart `uvicorn`.                           |

---

## 9. Project layout

```
nexusexporter-clean/
├── main.py                  # FastAPI app: routes, models, OAuth, ingestion
├── test_api_ingest.py       # API-ingestion test suite
├── templates/               # Jinja2 templates (home, dashboard, league_*, etc.)
├── static/                  # CSS / JS / images
├── scripts/
│   ├── init_db.py           # bootstrap / reset the database
│   └── setup.ps1            # Windows one-shot setup helper
├── Dockerfile               # production container
├── docker-compose.yml       # local dev stack (db + web)
├── Makefile                 # common commands (Linux/macOS/WSL/Git-Bash)
├── requirements.txt         # pinned Python deps
├── .env.example             # environment template — copy to .env
└── README.md
```

---

## License

Private project — all rights reserved.
