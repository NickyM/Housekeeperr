from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

import httpx

from . import db
from .clients import (
    JellyfinClient, PlexClient, RadarrClient, SeerrClient, SonarrClient, TMDBClient,
)


def _radarr_size(movie: dict[str, Any]) -> int:
    stats = movie.get("statistics") or {}
    return int(stats.get("sizeOnDisk") or movie.get("sizeOnDisk") or 0)


def _sonarr_size(series: dict[str, Any]) -> int:
    stats = series.get("statistics") or {}
    return int(stats.get("sizeOnDisk") or 0)


def _poster(images: list[dict[str, Any]] | None) -> str | None:
    if not images:
        return None
    for img in images:
        if img.get("coverType") == "poster":
            return img.get("remoteUrl") or img.get("url")
    return None


def _extract_providers(watch_results: dict[str, Any], region: str,
                       wanted: set[int]) -> tuple[list[int], list[str]]:
    region_data = watch_results.get(region) or {}
    found_ids: list[int] = []
    found_names: list[str] = []
    seen: set[int] = set()
    # 'flatrate' = subscription streaming. Also include 'free' & 'ads' if present.
    for bucket in ("flatrate", "free", "ads"):
        for prov in region_data.get(bucket, []) or []:
            pid = prov.get("provider_id")
            if pid in wanted and pid not in seen:
                seen.add(pid)
                found_ids.append(pid)
                found_names.append(prov.get("provider_name") or str(pid))
    return found_ids, found_names


def _lookup_in(idx: dict[str, Any] | None, kind: str,
               tmdb_id: int | None, tvdb_id: int | None,
               imdb_id: str | None) -> dict[str, Any] | None:
    if not idx:
        return None
    bucket = idx.get(kind) or {}
    if tmdb_id and str(tmdb_id) in bucket.get("tmdb", {}):
        return bucket["tmdb"][str(tmdb_id)]
    if tvdb_id and str(tvdb_id) in bucket.get("tvdb", {}):
        return bucket["tvdb"][str(tvdb_id)]
    if imdb_id and imdb_id in bucket.get("imdb", {}):
        return bucket["imdb"][imdb_id]
    return None


def _merge_watched(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """OR the watched flag, max the view_count, prefer non-empty extras."""
    blank = {"watched": 0, "view_count": 0, "total_episodes": None,
             "last_viewed_at": None, "rating_key": None, "jellyfin_item_id": None}
    if not a and not b:
        return blank
    a = a or {}
    b = b or {}

    def w_max(wa: int, wb: int) -> int:
        # 1 (fully watched) > 2 (in progress) > 0 (unknown)
        if wa == 1 or wb == 1:
            return 1
        if wa == 2 or wb == 2:
            return 2
        return 0

    return {
        "watched": w_max(int(a.get("watched") or 0), int(b.get("watched") or 0)),
        "view_count": max(int(a.get("view_count") or 0), int(b.get("view_count") or 0)),
        "total_episodes": a.get("total_episodes") or b.get("total_episodes"),
        "last_viewed_at": max(
            (a.get("last_viewed_at") or ""), (b.get("last_viewed_at") or ""),
        ) or None,
        "rating_key": a.get("rating_key") or b.get("rating_key"),
        "jellyfin_item_id": a.get("jellyfin_item_id") or b.get("jellyfin_item_id"),
    }


def _lookup_watched(plex_idx: dict[str, Any] | None,
                    jelly_idx: dict[str, Any] | None,
                    kind: str,
                    tmdb_id: int | None, tvdb_id: int | None,
                    imdb_id: str | None) -> dict[str, Any]:
    plex_info = _lookup_in(plex_idx, kind, tmdb_id, tvdb_id, imdb_id)
    jelly_info = _lookup_in(jelly_idx, kind, tmdb_id, tvdb_id, imdb_id)
    return _merge_watched(plex_info, jelly_info)


def _lookup_requesters(req_idx: dict[str, dict[str, list[str]]] | None,
                       kind: str, tmdb_id: int | None) -> list[str]:
    if not req_idx or not tmdb_id:
        return []
    bucket = req_idx.get(kind) or {}
    return list(bucket.get(str(tmdb_id), []))


async def _process_batch(items: list[dict[str, Any]], kind: str, source: str,
                          tmdb: TMDBClient, region: str, wanted: set[int],
                          counter: dict[str, int],
                          plex_idx: dict[str, Any] | None,
                          jelly_idx: dict[str, Any] | None,
                          req_idx: dict[str, dict[str, list[str]]] | None) -> None:
    sem = asyncio.Semaphore(8)

    async with httpx.AsyncClient(timeout=30) as client:
        async def one(it: dict[str, Any]) -> None:
            async with sem:
                tmdb_id = it.get("tmdbId") or 0
                providers_ids: list[int] = []
                provider_names: list[str] = []
                if tmdb_id:
                    try:
                        results = await tmdb.watch_providers(kind, tmdb_id, client)
                        providers_ids, provider_names = _extract_providers(
                            results, region, wanted
                        )
                    except Exception:
                        providers_ids, provider_names = [], []

                size = _radarr_size(it) if source == "radarr" else _sonarr_size(it)
                slug = it.get("titleSlug") or ""
                if source == "radarr":
                    arr_path = f"movie/{slug or tmdb_id}"
                else:
                    arr_path = f"series/{slug}" if slug else ""
                watched = _lookup_watched(
                    plex_idx, jelly_idx, kind,
                    int(tmdb_id) if tmdb_id else None,
                    int(it.get("tvdbId") or 0) or None,
                    it.get("imdbId") or None,
                )
                requesters = _lookup_requesters(
                    req_idx, kind, int(tmdb_id) if tmdb_id else None
                )
                db.upsert_item({
                    "source": source,
                    "source_id": int(it["id"]),
                    "tmdb_id": int(tmdb_id) if tmdb_id else None,
                    "title": it.get("title") or "(untitled)",
                    "year": int(it.get("year") or 0) or None,
                    "kind": kind,
                    "poster_url": _poster(it.get("images")),
                    "providers": json.dumps(providers_ids),
                    "provider_names": json.dumps(provider_names),
                    "size_bytes": size,
                    "arr_path": arr_path,
                    "watched": watched["watched"],
                    "view_count": watched["view_count"],
                    "total_episodes": watched["total_episodes"],
                    "last_viewed_at": watched["last_viewed_at"],
                    "plex_rating_key": watched.get("rating_key"),
                    "jellyfin_item_id": watched.get("jellyfin_item_id"),
                    "requesters": json.dumps(requesters),
                })
                counter["done"] += 1
                db.set_scan_status(processed=counter["done"])

        await asyncio.gather(*(one(it) for it in items))


async def run_scan() -> None:
    cfg = db.get_config()
    region = cfg.get("region") or "US"
    wanted = set(int(p) for p in (cfg.get("providers") or []))

    tmdb_key = cfg.get("tmdb_api_key") or ""
    if not tmdb_key:
        db.set_scan_status(running=0, phase="error",
                           error="TMDB API key is not configured.",
                           finished_at=_now())
        return
    if not wanted:
        db.set_scan_status(running=0, phase="error",
                           error="No providers selected.",
                           finished_at=_now())
        return

    tmdb = TMDBClient(tmdb_key)

    db.set_scan_status(running=1, phase="fetching", processed=0, total=0,
                       started_at=_now(), finished_at=None, error=None)

    try:
        movies: list[dict[str, Any]] = []
        series: list[dict[str, Any]] = []

        if cfg.get("radarr_url") and cfg.get("radarr_api_key"):
            radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
            try:
                movies = await radarr.list_movies()
            except Exception as e:
                db.set_scan_status(error=f"Radarr fetch failed: {e}")
        if cfg.get("sonarr_url") and cfg.get("sonarr_api_key"):
            sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
            try:
                series = await sonarr.list_series()
            except Exception as e:
                db.set_scan_status(error=f"Sonarr fetch failed: {e}")

        plex_idx: dict[str, Any] | None = None
        jelly_idx: dict[str, Any] | None = None
        req_idx: dict[str, dict[str, list[str]]] | None = None
        if cfg.get("plex_url") and cfg.get("plex_token"):
            db.set_scan_status(phase="plex")
            try:
                plex_idx = await PlexClient(cfg["plex_url"], cfg["plex_token"]).watched_index()
            except Exception as e:
                db.set_scan_status(error=f"Plex fetch failed: {e}")
        if cfg.get("jellyfin_url") and cfg.get("jellyfin_api_key"):
            db.set_scan_status(phase="jellyfin")
            try:
                jelly_idx = await JellyfinClient(
                    cfg["jellyfin_url"], cfg["jellyfin_api_key"],
                    user_id=cfg.get("jellyfin_user_id") or "",
                ).watched_index()
            except Exception as e:
                db.set_scan_status(error=f"Jellyfin fetch failed: {e}")
        if cfg.get("seerr_url") and cfg.get("seerr_api_key"):
            db.set_scan_status(phase="seerr")
            try:
                req_idx = await SeerrClient(cfg["seerr_url"], cfg["seerr_api_key"]).request_index()
            except Exception as e:
                db.set_scan_status(error=f"Seerr fetch failed: {e}")

        total = len(movies) + len(series)
        counter = {"done": 0}
        db.set_scan_status(phase="resolving", total=total, processed=0)

        # Drop entries that no longer exist in the *arrs
        if movies:
            db.clear_items("radarr")
        if series:
            db.clear_items("sonarr")

        await _process_batch(movies, "movie", "radarr", tmdb, region, wanted,
                             counter, plex_idx, jelly_idx, req_idx)
        await _process_batch(series, "tv", "sonarr", tmdb, region, wanted,
                             counter, plex_idx, jelly_idx, req_idx)

        db.set_scan_status(running=0, phase="done", finished_at=_now())
    except Exception as e:
        db.set_scan_status(running=0, phase="error",
                           error=f"{e}\n{traceback.format_exc()}",
                           finished_at=_now())


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
