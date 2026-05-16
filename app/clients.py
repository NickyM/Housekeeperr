from __future__ import annotations

import httpx
from typing import Any


class ArrError(RuntimeError):
    pass


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


async def _ping(url: str, headers: dict[str, str] | None = None,
                params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe an endpoint and return diagnostic info so the UI can show the real error."""
    out: dict[str, Any] = {"ok": False, "status": None, "url": url, "error": None}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers=headers or {}, params=params)
        out["status"] = r.status_code
        out["ok"] = 200 <= r.status_code < 300
        if not out["ok"]:
            snippet = r.text.strip().replace("\n", " ")[:200]
            out["error"] = f"HTTP {r.status_code}{(': ' + snippet) if snippet else ''}"
    except httpx.ConnectError as e:
        out["error"] = f"Connection refused / DNS failure: {e}"
    except httpx.ConnectTimeout:
        out["error"] = "Connection timed out (host unreachable?)"
    except httpx.ReadTimeout:
        out["error"] = "Read timed out"
    except httpx.InvalidURL as e:
        out["error"] = f"Invalid URL: {e}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------- Radarr ------------------------------------------------------

class RadarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = _normalize_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "accept": "application/json"}

    async def list_movies(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base}/api/v3/movie", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def delete_movie(self, movie_id: int, delete_files: bool = True,
                           add_exclusion: bool = False) -> None:
        params = {
            "deleteFiles": "true" if delete_files else "false",
            "addImportExclusion": "true" if add_exclusion else "false",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.delete(
                f"{self.base}/api/v3/movie/{movie_id}",
                headers=self._headers(),
                params=params,
            )
            if r.status_code >= 400:
                raise ArrError(f"Radarr delete failed: {r.status_code} {r.text}")

    async def ping(self) -> dict[str, Any]:
        return await _ping(f"{self.base}/api/v3/system/status", self._headers())


# ---------------- Sonarr ------------------------------------------------------

class SonarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = _normalize_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "accept": "application/json"}

    async def list_series(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{self.base}/api/v3/series", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def delete_series(self, series_id: int, delete_files: bool = True,
                            add_exclusion: bool = False) -> None:
        params = {
            "deleteFiles": "true" if delete_files else "false",
            "addImportListExclusion": "true" if add_exclusion else "false",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.delete(
                f"{self.base}/api/v3/series/{series_id}",
                headers=self._headers(),
                params=params,
            )
            if r.status_code >= 400:
                raise ArrError(f"Sonarr delete failed: {r.status_code} {r.text}")

    async def list_episodes(self, series_id: int) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{self.base}/api/v3/episode",
                headers=self._headers(),
                params={"seriesId": series_id},
            )
            r.raise_for_status()
            return r.json()

    async def delete_episode_files(self, file_ids: list[int]) -> int:
        """Delete the given episode files. Returns count of successful deletes."""
        if not file_ids:
            return 0
        # Try the bulk endpoint first; fall back to single deletes if it isn't there.
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.request(
                "DELETE",
                f"{self.base}/api/v3/episodefile/bulk",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"episodeFileIds": file_ids},
            )
            if r.status_code < 400:
                return len(file_ids)
            # Fallback: per-id deletes
            ok = 0
            for fid in file_ids:
                rr = await c.delete(
                    f"{self.base}/api/v3/episodefile/{fid}",
                    headers=self._headers(),
                )
                if rr.status_code < 400:
                    ok += 1
            return ok

    async def ping(self) -> dict[str, Any]:
        return await _ping(f"{self.base}/api/v3/system/status", self._headers())


# ---------------- TMDB --------------------------------------------------------

class TMDBClient:
    BASE = "https://api.themoviedb.org/3"
    IMG = "https://image.tmdb.org/t/p/w342"

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout

    def _params(self, **extra: Any) -> dict[str, Any]:
        p = {"api_key": self.api_key}
        p.update(extra)
        return p

    async def watch_providers(self, kind: str, tmdb_id: int,
                              client: httpx.AsyncClient) -> dict[str, Any]:
        endpoint = "movie" if kind == "movie" else "tv"
        r = await client.get(
            f"{self.BASE}/{endpoint}/{tmdb_id}/watch/providers",
            params=self._params(),
        )
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json().get("results", {})

    async def list_providers(self, kind: str, region: str) -> list[dict[str, Any]]:
        endpoint = "movie" if kind == "movie" else "tv"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{self.BASE}/watch/providers/{endpoint}",
                params=self._params(watch_region=region),
            )
            r.raise_for_status()
            return r.json().get("results", [])

    async def list_regions(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{self.BASE}/watch/providers/regions",
                params=self._params(),
            )
            r.raise_for_status()
            return r.json().get("results", [])

    async def ping(self) -> dict[str, Any]:
        return await _ping(f"{self.BASE}/configuration", params=self._params())


# ---------------- Plex --------------------------------------------------------

import re as _re
from datetime import datetime, timezone


def _parse_plex_guids(item: dict[str, Any]) -> dict[str, str]:
    """Return {'tmdb':'278', 'tvdb':'1252', 'imdb':'tt0111161'} from a Plex item."""
    out: dict[str, str] = {}
    # Modern Plex agent: Guid array with id like "tmdb://278"
    for g in item.get("Guid") or []:
        gid = g.get("id") or ""
        m = _re.match(r"^(tmdb|tvdb|imdb)://([^?#]+)", gid)
        if m:
            out.setdefault(m.group(1), m.group(2))
    # Legacy agent: single guid like "com.plexapp.agents.themoviedb://12345?lang=en"
    legacy = item.get("guid") or ""
    if "themoviedb" in legacy:
        m = _re.search(r"themoviedb://(\d+)", legacy)
        if m:
            out.setdefault("tmdb", m.group(1))
    elif "thetvdb" in legacy:
        m = _re.search(r"thetvdb://(\d+)", legacy)
        if m:
            out.setdefault("tvdb", m.group(1))
    elif "imdb" in legacy:
        m = _re.search(r"imdb://(tt\d+)", legacy)
        if m:
            out.setdefault("imdb", m.group(1))
    return out


def _iso_from_epoch(ts: Any) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


class PlexClient:
    """Talks to a Plex Media Server over its HTTP API."""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base = _normalize_url(base_url)
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Plex-Token": self.token, "Accept": "application/json"}

    async def libraries(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        r = await client.get(f"{self.base}/library/sections", headers=self._headers())
        r.raise_for_status()
        data = r.json().get("MediaContainer", {}).get("Directory", [])
        return [
            {"key": d.get("key"), "type": d.get("type"), "title": d.get("title")}
            for d in data
        ]

    async def items(self, section_key: str, type_id: int,
                    client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """type_id: 1=movie, 2=show. Returns the raw Plex Metadata entries."""
        # Plex supports paging via X-Plex-Container-Start/Size; default returns all.
        params = {"type": type_id, "includeGuids": 1}
        r = await client.get(
            f"{self.base}/library/sections/{section_key}/all",
            headers=self._headers(),
            params=params,
        )
        r.raise_for_status()
        return r.json().get("MediaContainer", {}).get("Metadata", []) or []

    async def watched_index(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Returns a nested lookup:
           {
             'movie': {'tmdb': {id: info}, 'tvdb': {...}, 'imdb': {...}},
             'tv':    {'tmdb': {id: info}, 'tvdb': {...}, 'imdb': {...}},
           }
           where info = {watched(0/1/2), view_count, total_episodes, last_viewed_at}
        """
        idx: dict[str, dict[str, dict[str, Any]]] = {
            "movie": {"tmdb": {}, "tvdb": {}, "imdb": {}},
            "tv":    {"tmdb": {}, "tvdb": {}, "imdb": {}},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            libs = await self.libraries(c)
            for lib in libs:
                ltype = lib.get("type")
                key = lib.get("key")
                if not key:
                    continue
                if ltype == "movie":
                    items = await self.items(key, 1, c)
                    bucket = idx["movie"]
                    for it in items:
                        info = {
                            "watched": 1 if int(it.get("viewCount") or 0) > 0 else 0,
                            "view_count": int(it.get("viewCount") or 0),
                            "total_episodes": None,
                            "last_viewed_at": _iso_from_epoch(it.get("lastViewedAt")),
                            "rating_key": str(it.get("ratingKey") or ""),
                        }
                        for src, val in _parse_plex_guids(it).items():
                            bucket[src][val] = info
                elif ltype == "show":
                    items = await self.items(key, 2, c)
                    bucket = idx["tv"]
                    for it in items:
                        total = int(it.get("leafCount") or 0)
                        viewed = int(it.get("viewedLeafCount") or 0)
                        if total > 0 and viewed >= total:
                            watched = 1
                        elif viewed > 0:
                            watched = 2
                        else:
                            watched = 0
                        info = {
                            "watched": watched,
                            "view_count": viewed,
                            "total_episodes": total or None,
                            "last_viewed_at": _iso_from_epoch(it.get("lastViewedAt")),
                            "rating_key": str(it.get("ratingKey") or ""),
                        }
                        for src, val in _parse_plex_guids(it).items():
                            bucket[src][val] = info
        return idx

    async def show_episodes(self, rating_key: str) -> list[dict[str, Any]]:
        """Return the flat list of episodes for a show with watch state.
        Each entry: {season:int, episode:int, watched:bool, view_count:int}."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{self.base}/library/metadata/{rating_key}/allLeaves",
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json().get("MediaContainer", {}).get("Metadata", []) or []
        out: list[dict[str, Any]] = []
        for ep in data:
            season = ep.get("parentIndex")
            episode = ep.get("index")
            if season is None or episode is None:
                continue
            view_count = int(ep.get("viewCount") or 0)
            out.append({
                "season": int(season),
                "episode": int(episode),
                "watched": view_count > 0,
                "view_count": view_count,
            })
        return out

    async def ping(self) -> dict[str, Any]:
        return await _ping(f"{self.base}/identity", headers=self._headers())
