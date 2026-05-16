from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .clients import (
    JellyfinClient, PlexClient, RadarrClient, SeerrClient, SonarrClient, TMDBClient,
)
from .scanner import run_scan


async def _jellyfin_show_episodes(jelly: JellyfinClient,
                                 tmdb_id: int | None,
                                 tvdb_id: int | None,
                                 imdb_id: str | None) -> list[dict[str, Any]]:
    """Find a Jellyfin show by external ID and return its per-episode watch state."""
    idx = await jelly.watched_index()
    tv = idx.get("tv") or {}
    info = None
    if tmdb_id and str(tmdb_id) in tv.get("tmdb", {}):
        info = tv["tmdb"][str(tmdb_id)]
    elif tvdb_id and str(tvdb_id) in tv.get("tvdb", {}):
        info = tv["tvdb"][str(tvdb_id)]
    elif imdb_id and imdb_id in tv.get("imdb", {}):
        info = tv["imdb"][imdb_id]
    if not info or not info.get("jellyfin_item_id"):
        return []
    return await jelly.show_episodes(info["jellyfin_item_id"])


async def _delete_watched_episodes_for(source_id: int) -> dict[str, Any]:
    """Delete only watched-on-Plex episode files for a Sonarr series.
    Returns {'deleted_files': N, 'deleted_episodes': M} or raises HTTPException."""
    cfg = db.get_config()
    if not (cfg.get("sonarr_url") and cfg.get("sonarr_api_key")):
        raise HTTPException(400, "Sonarr not configured")
    has_plex = bool(cfg.get("plex_url") and cfg.get("plex_token"))
    has_jelly = bool(cfg.get("jellyfin_url") and cfg.get("jellyfin_api_key"))
    if not (has_plex or has_jelly):
        raise HTTPException(400, "Neither Plex nor Jellyfin is configured")
    item = db.get_item("sonarr", source_id)
    if not item:
        raise HTTPException(404, "item not found in scan cache — run a scan first")
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])

    watched_keys: set[tuple[int, int]] = set()

    # Path 1: Plex (preferred since per-episode watch state is exact via allLeaves)
    plex = PlexClient(cfg["plex_url"], cfg["plex_token"]) if has_plex else None
    rk = item.get("plex_rating_key") if has_plex else None
    if has_plex and not rk:
        idx = await plex.watched_index()
        tv = idx.get("tv") or {}
        tmdb_id = item.get("tmdb_id")
        info: dict[str, Any] | None = None
        if tmdb_id and str(tmdb_id) in tv.get("tmdb", {}):
            info = tv["tmdb"][str(tmdb_id)]
        if not info:
            try:
                sonarr_series = await sonarr.list_series()
                match = next((s for s in sonarr_series if int(s.get("id") or 0) == source_id), None)
            except Exception:
                match = None
            if match:
                tvdb_id = match.get("tvdbId")
                imdb_id = match.get("imdbId")
                if tvdb_id and str(tvdb_id) in tv.get("tvdb", {}):
                    info = tv["tvdb"][str(tvdb_id)]
                elif imdb_id and imdb_id in tv.get("imdb", {}):
                    info = tv["imdb"][imdb_id]
        if info and info.get("rating_key"):
            rk = info["rating_key"]
            db.set_plex_rating_key("sonarr", source_id, rk)
    if has_plex and rk:
        plex_eps = await plex.show_episodes(rk)
        for e in plex_eps:
            if e["watched"]:
                watched_keys.add((e["season"], e["episode"]))

    # Path 2: Jellyfin (used when Plex isn't configured or didn't match)
    if has_jelly and not watched_keys:
        jelly = JellyfinClient(
            cfg["jellyfin_url"], cfg["jellyfin_api_key"],
            user_id=cfg.get("jellyfin_user_id") or "",
        )
        try:
            sonarr_series = await sonarr.list_series()
            match = next((s for s in sonarr_series if int(s.get("id") or 0) == source_id), None)
        except Exception:
            match = None
        tmdb_id = item.get("tmdb_id")
        tvdb_id = (match or {}).get("tvdbId")
        imdb_id = (match or {}).get("imdbId")
        jelly_eps = await _jellyfin_show_episodes(jelly, tmdb_id, tvdb_id, imdb_id)
        for e in jelly_eps:
            if e["watched"]:
                watched_keys.add((e["season"], e["episode"]))

    if not watched_keys:
        raise HTTPException(400,
            "couldn't determine watched episodes from any configured source")

    sonarr_eps = await sonarr.list_episodes(source_id)
    file_ids: set[int] = set()
    matched_eps = 0
    for ep in sonarr_eps:
        key = (int(ep.get("seasonNumber") or -1), int(ep.get("episodeNumber") or -1))
        if key not in watched_keys:
            continue
        fid = int(ep.get("episodeFileId") or 0)
        if fid > 0 and ep.get("hasFile"):
            file_ids.add(fid)
            matched_eps += 1
    deleted = await sonarr.delete_episode_files(sorted(file_ids))
    return {"deleted_files": deleted, "deleted_episodes": matched_eps}

VERSION = "0.1.1"

db.init()

app = FastAPI(title="Housekeeperr", version=VERSION, docs_url="/api/docs")


@app.get("/api/version")
async def version() -> dict[str, str]:
    return {"version": VERSION}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ----- Pages ------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "settings.html")


@app.get("/about")
async def about_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "about.html")


# ----- Config -----------------------------------------------------------------

class ConfigPayload(BaseModel):
    radarr_url: str | None = None
    radarr_api_key: str | None = None
    sonarr_url: str | None = None
    sonarr_api_key: str | None = None
    tmdb_api_key: str | None = None
    plex_url: str | None = None
    plex_token: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    jellyfin_user_id: str | None = None
    seerr_url: str | None = None
    seerr_api_key: str | None = None
    region: str | None = None
    providers: list[int] | None = None


_SECRET_KEYS = (
    "radarr_api_key", "sonarr_api_key", "tmdb_api_key",
    "plex_token", "jellyfin_api_key", "seerr_api_key",
)


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    cfg = db.get_config()
    # Mask the keys in the GET response so they don't render in plaintext on page load,
    # but keep them retrievable by length so the UI can show "configured" state.
    masked = dict(cfg)
    for k in _SECRET_KEYS:
        if masked.get(k):
            masked[k] = "•" * 8
    return masked


@app.post("/api/config")
async def update_config(payload: ConfigPayload) -> dict[str, Any]:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    # Ignore unchanged masked values posted back from the UI
    for k in _SECRET_KEYS:
        if updates.get(k) and set(updates[k]) == {"•"}:
            updates.pop(k)
    db.set_config(updates)
    return await get_config()


@app.post("/api/config/test")
async def test_config(payload: ConfigPayload | None = None) -> dict[str, Any]:
    """Test connections. Uses values from the payload when provided, otherwise
    falls back to the saved config, so the user doesn't have to Save first."""
    cfg = db.get_config()
    overrides = (payload.model_dump() if payload else {}) or {}
    # Don't accept the masked placeholder as a real key
    for k in _SECRET_KEYS:
        v = overrides.get(k)
        if v and set(v) == {"•"}:
            overrides[k] = None
    eff = {**cfg, **{k: v for k, v in overrides.items() if v is not None}}

    out: dict[str, Any] = {
        "radarr": None, "sonarr": None, "tmdb": None,
        "plex": None, "jellyfin": None, "seerr": None,
    }
    if eff.get("radarr_url") and eff.get("radarr_api_key"):
        out["radarr"] = await RadarrClient(eff["radarr_url"], eff["radarr_api_key"]).ping()
    if eff.get("sonarr_url") and eff.get("sonarr_api_key"):
        out["sonarr"] = await SonarrClient(eff["sonarr_url"], eff["sonarr_api_key"]).ping()
    if eff.get("tmdb_api_key"):
        out["tmdb"] = await TMDBClient(eff["tmdb_api_key"]).ping()
    if eff.get("plex_url") and eff.get("plex_token"):
        out["plex"] = await PlexClient(eff["plex_url"], eff["plex_token"]).ping()
    if eff.get("jellyfin_url") and eff.get("jellyfin_api_key"):
        out["jellyfin"] = await JellyfinClient(
            eff["jellyfin_url"], eff["jellyfin_api_key"]
        ).ping()
    if eff.get("seerr_url") and eff.get("seerr_api_key"):
        out["seerr"] = await SeerrClient(eff["seerr_url"], eff["seerr_api_key"]).ping()
    return out


# ----- Providers / regions catalog -------------------------------------------

@app.get("/api/providers")
async def providers(region: str | None = None) -> dict[str, Any]:
    cfg = db.get_config()
    if not cfg.get("tmdb_api_key"):
        raise HTTPException(400, "TMDB API key not configured")
    region = region or cfg.get("region") or "US"
    tmdb = TMDBClient(cfg["tmdb_api_key"])
    movie_list = await tmdb.list_providers("movie", region)
    tv_list = await tmdb.list_providers("tv", region)
    seen: dict[int, dict[str, Any]] = {}
    for prov in movie_list + tv_list:
        pid = prov.get("provider_id")
        if pid and pid not in seen:
            seen[pid] = {
                "id": pid,
                "name": prov.get("provider_name"),
                "logo": "https://image.tmdb.org/t/p/w92" + prov.get("logo_path", ""),
                "priority": prov.get("display_priority", 9999),
            }
    return {
        "region": region,
        "providers": sorted(seen.values(), key=lambda x: x["priority"]),
    }


@app.get("/api/regions")
async def regions() -> list[dict[str, Any]]:
    cfg = db.get_config()
    if not cfg.get("tmdb_api_key"):
        raise HTTPException(400, "TMDB API key not configured")
    out = await TMDBClient(cfg["tmdb_api_key"]).list_regions()
    return sorted(
        [{"code": r.get("iso_3166_1"), "name": r.get("english_name")} for r in out],
        key=lambda r: r["name"] or "",
    )


# ----- Scan -------------------------------------------------------------------

_scan_lock = asyncio.Lock()


async def _start_scan() -> None:
    if _scan_lock.locked():
        return
    async with _scan_lock:
        await run_scan()


@app.post("/api/scan")
async def trigger_scan(bg: BackgroundTasks) -> dict[str, Any]:
    status = db.get_scan_status()
    if status.get("running"):
        return {"already_running": True, **status}
    bg.add_task(_start_scan)
    return {"started": True}


@app.get("/api/scan/status")
async def scan_status() -> dict[str, Any]:
    return db.get_scan_status()


# ----- Items / actions --------------------------------------------------------

@app.get("/api/items")
async def list_items(include_ignored: bool = False,
                     provider: int | None = None,
                     mode: str = "streaming") -> dict[str, Any]:
    if mode not in ("streaming", "watched", "both", "all"):
        raise HTTPException(400, "mode must be one of: streaming, watched, both, all")
    filt = [provider] if provider else None
    items = db.list_items(include_ignored=include_ignored, provider_filter=filt, mode=mode)
    cfg = db.get_config()
    base_links = {
        "radarr": cfg.get("radarr_url", "").rstrip("/"),
        "sonarr": cfg.get("sonarr_url", "").rstrip("/"),
    }
    for it in items:
        base = base_links.get(it["source"]) or ""
        path = it.get("arr_path") or ""
        it["arr_url"] = f"{base}/{path}" if base and path else None
    return {"items": items, "count": len(items)}


@app.post("/api/items/{source}/{source_id}/ignore")
async def ignore_item(source: str, source_id: int) -> dict[str, Any]:
    if source not in ("radarr", "sonarr"):
        raise HTTPException(400, "bad source")
    db.ignore(source, source_id)
    return {"ok": True, "ignored": True}


@app.post("/api/items/{source}/{source_id}/unignore")
async def unignore_item(source: str, source_id: int) -> dict[str, Any]:
    if source not in ("radarr", "sonarr"):
        raise HTTPException(400, "bad source")
    db.unignore(source, source_id)
    return {"ok": True, "ignored": False}


class BulkRef(BaseModel):
    source: str
    source_id: int


class BulkPayload(BaseModel):
    action: str  # "ignore" | "unignore" | "delete"
    items: list[BulkRef]
    add_exclusion: bool = False
    tv_episodes_mode: str = "all"  # "all" | "watched" (applies to Sonarr items only)


@app.post("/api/items/bulk")
async def bulk_items(payload: BulkPayload) -> dict[str, Any]:
    if payload.action not in ("ignore", "unignore", "delete"):
        raise HTTPException(400, "bad action")
    if payload.tv_episodes_mode not in ("all", "watched"):
        raise HTTPException(400, "tv_episodes_mode must be 'all' or 'watched'")

    cfg = db.get_config()
    radarr = None
    sonarr = None
    if payload.action == "delete":
        if any(it.source == "radarr" for it in payload.items):
            if not (cfg.get("radarr_url") and cfg.get("radarr_api_key")):
                raise HTTPException(400, "Radarr not configured")
            radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
        if any(it.source == "sonarr" for it in payload.items):
            if not (cfg.get("sonarr_url") and cfg.get("sonarr_api_key")):
                raise HTTPException(400, "Sonarr not configured")
            sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])

    sem = asyncio.Semaphore(4)

    async def one(ref: BulkRef) -> dict[str, Any]:
        if ref.source not in ("radarr", "sonarr"):
            return {"source": ref.source, "source_id": ref.source_id,
                    "ok": False, "error": "bad source"}
        async with sem:
            try:
                if payload.action == "ignore":
                    db.ignore(ref.source, ref.source_id)
                    return {"source": ref.source, "source_id": ref.source_id, "ok": True}
                if payload.action == "unignore":
                    db.unignore(ref.source, ref.source_id)
                    return {"source": ref.source, "source_id": ref.source_id, "ok": True}
                # delete
                if ref.source == "radarr":
                    await radarr.delete_movie(ref.source_id, delete_files=True,
                                              add_exclusion=payload.add_exclusion)
                    db.delete_item(ref.source, ref.source_id)
                    return {"source": ref.source, "source_id": ref.source_id,
                            "ok": True, "mode": "all"}
                # sonarr
                if payload.tv_episodes_mode == "watched":
                    try:
                        res = await _delete_watched_episodes_for(ref.source_id)
                    except HTTPException as he:
                        return {"source": ref.source, "source_id": ref.source_id,
                                "ok": False, "error": he.detail}
                    # Series record stays — local item also stays for next scan to reconcile.
                    return {"source": ref.source, "source_id": ref.source_id,
                            "ok": True, "mode": "watched", **res}
                await sonarr.delete_series(ref.source_id, delete_files=True,
                                           add_exclusion=payload.add_exclusion)
                db.delete_item(ref.source, ref.source_id)
                return {"source": ref.source, "source_id": ref.source_id,
                        "ok": True, "mode": "all"}
            except Exception as e:
                return {"source": ref.source, "source_id": ref.source_id,
                        "ok": False, "error": str(e)}

    results = await asyncio.gather(*(one(it) for it in payload.items))
    return {
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
    }


@app.delete("/api/items/{source}/{source_id}")
async def delete_item(source: str, source_id: int,
                      add_exclusion: bool = False,
                      episodes: str = "all") -> dict[str, Any]:
    """For Sonarr items, episodes=watched deletes only watched episode files
    (keeping the series record). episodes=all deletes the whole series."""
    if episodes not in ("all", "watched"):
        raise HTTPException(400, "episodes must be 'all' or 'watched'")
    cfg = db.get_config()
    if source == "radarr":
        if not (cfg.get("radarr_url") and cfg.get("radarr_api_key")):
            raise HTTPException(400, "Radarr not configured")
        await RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"]).delete_movie(
            source_id, delete_files=True, add_exclusion=add_exclusion
        )
        db.delete_item(source, source_id)
        return {"ok": True, "deleted": True, "mode": "all"}
    elif source == "sonarr":
        if not (cfg.get("sonarr_url") and cfg.get("sonarr_api_key")):
            raise HTTPException(400, "Sonarr not configured")
        if episodes == "watched":
            res = await _delete_watched_episodes_for(source_id)
            # Series stays in Sonarr; refresh of local watched state happens on next scan.
            return {"ok": True, "deleted": False, "mode": "watched", **res}
        await SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"]).delete_series(
            source_id, delete_files=True, add_exclusion=add_exclusion
        )
        db.delete_item(source, source_id)
        return {"ok": True, "deleted": True, "mode": "all"}
    else:
        raise HTTPException(400, "bad source")
