<p align="center">
  <img src="static/favicon.svg" alt="Housekeeperr logo" width="128" height="128" />
</p>

# Housekeeperr

Self-hosted web app that scans your **Radarr** and **Sonarr** libraries,
cross-references each title against streaming services (Netflix, Disney+, …)
in your region via **TMDB**, optionally checks watch status on **Plex**
and/or **Jellyfin**, and tags items with who requested them via
**[Seerr](https://github.com/seerr-team/seerr)** (formerly Overseerr /
Jellyseerr). Surfaces cleanup candidates in a single page
with bulk ignore / delete.

> "What of my collection is now on Netflix and Disney+ in my country, that
> I've already finished watching, and who asked for it in the first place?"
> — Housekeeperr answers that, with one click to free the disk.

## Features

- 📚 Scans every movie in Radarr and every show in Sonarr by their TMDB id.
- 📺 Resolves streaming availability per **region** via the TMDB
  watch-providers endpoint (the same data JustWatch publishes — free, official).
- 🎬 Optional **Plex** and/or **Jellyfin** integration: tags items as
  Watched / In progress and enables a "Watched & on streaming" cleanup
  view. If both are configured, watch state is unioned (watched by *either*
  counts as watched). Jellyfin aggregates across all non-hidden users by
  default (configurable).
- 🙋 Optional **[Seerr](https://github.com/seerr-team/seerr)** integration
  (also works against legacy Overseerr / Jellyseerr installs — same API):
  each card is tagged
  with who requested the title (and a hover-tooltip listing all requesters
  if multiple).
- ✅ Per-item **Ignore** (persistent — survives rescans and re-additions) and
  **Delete** (calls Radarr/Sonarr's DELETE endpoint with `deleteFiles=true`).
  For TV shows, you can choose to delete only the *watched* episode files
  instead of the whole series; the affected episodes are then **unmonitored**
  in Sonarr so it doesn't re-acquire them.
- 📦 **Select mode** for bulk ignore/delete across many items at once.
- 🔗 Cards deep-link back into Radarr/Sonarr using each service's own
  `titleSlug`.
- 🛠️ Configure everything (Radarr/Sonarr/Plex URLs + API keys, region,
  providers) from the web UI. No config files to edit.
- 💾 All state — config, ignore list, scan cache, **TMDB watch-provider
  cache** — lives in a single SQLite file at `/data/housekeeper.db`. Mount
  it as a volume and everything persists across restarts and image
  upgrades. The TMDB cache has a 24-hour TTL per title and stores the full
  per-region response, so re-scans within a day are nearly free and don't
  re-hit TMDB.

## Quick start (Docker)

```bash
docker run -d \
  --name housekeeperr \
  -p 8765:8765 \
  -v housekeeperr-data:/data \
  --restart unless-stopped \
  ghcr.io/nickym/housekeeperr:latest
```

…then open <http://localhost:8765> and go to **Settings**.

### docker-compose

```yaml
services:
  housekeeperr:
    image: ghcr.io/nickym/housekeeperr:latest
    container_name: housekeeperr
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - housekeeperr-data:/data
    environment:
      TZ: Europe/Copenhagen

volumes:
  housekeeperr-data:
```

The repo ships with a working [`docker-compose.yml`](docker-compose.yml).

### TrueNAS SCALE

See [`truenas/README.md`](truenas/README.md) for step-by-step Custom App
deployment on TrueNAS SCALE 24.10+ ("Electric Eel").

### Run from source (no Docker)

Requires Python 3.11+.

```bash
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Or just double-click `run.bat` on Windows.

## Configuration

All configuration is done in the **Settings** page once the app is running.
No env vars are required for normal use; the only one that matters is
`HOUSEKEEPER_DATA_DIR` (default `/data` in Docker, `./data` from source) which
controls where the SQLite database is stored.

| Section | Field | Notes |
|---|---|---|
| Radarr | URL | e.g. `http://192.168.1.50:7878` (URL Base must be included if set) |
| Radarr | API key | Radarr → Settings → General |
| Sonarr | URL / API key | Same as above for Sonarr |
| TMDB | API key (v3) | Free — register at <https://www.themoviedb.org/settings/api> |
| Plex | URL | Optional. e.g. `http://192.168.1.50:32400` |
| Plex | X-Plex-Token | Optional. [How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |
| Jellyfin | URL | Optional. e.g. `http://192.168.1.50:8096` |
| Jellyfin | API key | Optional. Dashboard → Advanced → API Keys |
| Jellyfin | User ID | Optional. Blank = aggregate across all non-hidden users (recommended). Set to a specific user GUID to use that user's watch state only. |
| Seerr | URL | Optional. e.g. `http://192.168.1.50:5055`. Also works with legacy Overseerr / Jellyseerr (same API). |
| Seerr | API key | Optional. Settings → General → API Key |
| Region | | Country to check streaming availability in |
| Providers | | Pick the services to flag (Netflix, Disney+, etc.) |

An **About** page at `/about` links back to the repo, issue tracker,
container image, license and the upstream tools — useful when sharing
or troubleshooting.

After saving, click **Scan now** in the top bar. The scan fetches every
Radarr/Sonarr title, looks up its streaming providers on TMDB, pulls watch
state from Plex/Jellyfin in parallel, and pulls request history from
Seerr. Result: a grid of cards with provider, watched,
in-progress, and requester chips.

## Modes

The **Mode** dropdown on the Library page changes what's shown:

- **On a streaming service** — items currently available on any of your
  selected providers in your region. *(Default — the original use case.)*
- **Watched on Plex / Jellyfin** — items you've finished watching,
  regardless of streaming availability. The label adapts to whichever
  watch source(s) you've configured ("Watched on Plex", "Watched on
  Jellyfin", or "Watched on Plex / Jellyfin"). If neither is configured,
  this option is disabled.
- **Watched & on streaming (cleanup)** — the intersection: prime delete
  candidates.
- **All library items** — every item in Radarr/Sonarr, no filter.

## Persistent state

Everything that matters lives in `/data/housekeeper.db`:

- All settings (Radarr/Sonarr/TMDB/Plex URLs + API keys)
- The persistent **ignore list** (keyed by `(source, source_id)` so it
  survives re-adding the item)
- The cached scan results

Mount `/data` as a Docker volume or bind-mount and you can upgrade the image,
delete the container, etc., without losing anything.

> ⚠️ The `data/` directory is gitignored and dockerignored — it must not be
> committed to a public repo or baked into an image, because it contains
> your API keys.

## Architecture

```
Radarr ──┐
         │   (1) list libraries
Sonarr ──┤
         ▼
       FastAPI ──(2) TMDB watch/providers (per region)        ──┐
       backend     (3) Plex /library/sections/.../all          │
         │           (4) Jellyfin /Users/{id}/Items (per user) │
         │           (5) Seerr /api/v1/request                 │
         │                                                     ▼
         │                       SQLite (config + items + ignored)
         ▼
   Vanilla-JS frontend  ──── Library grid + Settings ────  Browser
```

Stack:

- **FastAPI** + **httpx** + stdlib **sqlite3** (~600 LOC of Python)
- **Vanilla JS / HTML / CSS** (no build step, no JS dependencies)
- **Single SQLite file** for state — no Postgres, Redis, or external broker

## Troubleshooting

**"FAIL — Connection refused / DNS failure"** in Test Connections
Radarr/Sonarr/Plex isn't reachable on that URL from inside the container.
From a container, `localhost` is the container itself, not the host. Use
the LAN IP or a Docker network alias.

**"FAIL — HTTP 401"**
The API key is wrong. Re-copy it from the *arr's Settings → General page.

**"FAIL — HTTP 404"**
The URL Base is set on Radarr/Sonarr but missing from the URL you entered.
Use `http://host:7878/radarr` (or whatever base you configured).

**TMDB rate limits / scan is slow**
The scanner runs 8 TMDB lookups in parallel. A library of 2 000 items
typically completes in under a minute. Subsequent scans within 24 hours
hit the local SQLite cache and skip TMDB entirely.

**"OK (HTTP 200)" for a wrong API key?**
Fixed — Jellyfin and Seerr tests now hit auth-required endpoints
(`/System/Info` and `/api/v1/auth/me` respectively), so a bad key
returns `FAIL — HTTP 401` instead of misleading you.

**Plex matches are missing**
Plex matching needs a valid GUID on the Plex item. The scanner handles both
modern Plex (`Guid[]` with `tmdb://`, `tvdb://`, `imdb://`) and legacy
agents (`com.plexapp.agents.themoviedb://…`). Old, never-refreshed metadata
without a usable ID will simply show no watched tag.

## License

[MIT](LICENSE) — do what you want; keep the copyright notice; no warranty.

Copyright (c) 2026 NickyM
