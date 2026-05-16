# Housekeeperr — project context for Claude

Self-hosted web app that scans Radarr/Sonarr libraries, cross-references
streaming availability via TMDB, and tracks Plex watch state. Lists cleanup
candidates with bulk ignore/delete. Repo: `github.com/NickyM/Housekeeperr`,
license: **GPL v3**.

## Stack

- Python 3.11+ (developed on 3.14), FastAPI + httpx + stdlib `sqlite3`.
- Vanilla JS / HTML / CSS in `static/`. **No build step, no JS dependencies.**
  Keep it that way.
- Single SQLite file is the entire datastore at `$HOUSEKEEPER_DATA_DIR/housekeeper.db`
  (defaults to `/data` in Docker, `./data` from source). Holds config (API keys),
  ignore list, and scan cache. The dir is git+docker-ignored.

## Layout

```
app/main.py       FastAPI routes (single file)
app/db.py         SQLite schema, migrations, helpers
app/clients.py    RadarrClient, SonarrClient, TMDBClient, PlexClient
app/scanner.py    Orchestrates radarr/sonarr/tmdb/plex fan-out
static/           Library + Settings pages (vanilla JS)
truenas/          TrueNAS Custom App install guide
Dockerfile        python:3.12-slim base, declares VOLUME /data
docker-compose.yml  Local + TrueNAS Custom App template
run.bat           Windows one-shot venv + uvicorn
```

## Development

- Use the `py` launcher on Windows, not bare `python`.
- `run.bat` creates `.venv`, installs deps, launches uvicorn on `:8765` with
  `--reload`.
- Smoke-test via FastAPI's `TestClient` rather than spawning uvicorn:
  ```python
  from fastapi.testclient import TestClient
  from app.main import app
  c = TestClient(app)
  ```
- Set `HOUSEKEEPER_DATA_DIR` to a temp path before tests to keep the real DB
  clean.

## Conventions

- All user-facing config lives in the DB and is set from the **Settings** page.
  Don't introduce env-var-only config.
- API keys are masked (`••••••••`) when read back from `/api/config`. The
  POST handler must ignore unchanged masked values so the UI doesn't blank
  them out on save.
- DB schema changes go in `db.init()` via the ALTER-if-missing migration
  block — never break existing installs.
- The frontend tolerates stale cache (e.g. items missing `plex_rating_key`
  from before that column existed). When in doubt, prefer to recover on
  demand rather than force a rescan.
- New deps: only add to `requirements.txt` if there's no stdlib equivalent.

## **Rule: keep README.md in sync**

Every feature change — new endpoint, new mode, new setting, new button,
changed default, new env var — also edits `README.md` in the same turn.
Sections most often affected: Features, Modes, Configuration table,
Troubleshooting. Skip for pure refactors with no user-visible change. See
the saved memory `feedback_update_readme.md`.

## Run & deploy

- Local dev: `run.bat`
- Docker: `docker compose up -d` (mounts a named volume at `/data`)
- TrueNAS SCALE 24.10+: see [`truenas/README.md`](truenas/README.md)
- Published image: `ghcr.io/nickym/housekeeperr:latest`
