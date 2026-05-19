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

    async def set_episodes_monitored(self, episode_ids: list[int],
                                      monitored: bool) -> int:
        """Bulk-set the monitored flag on episodes. Returns count updated."""
        if not episode_ids:
            return 0
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.put(
                f"{self.base}/api/v3/episode/monitor",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"episodeIds": episode_ids, "monitored": monitored},
            )
            if r.status_code < 400:
                return len(episode_ids)
            # Some Sonarr builds use POST or a different path; we don't bother
            # to fall back since /episode/monitor has been the v3 API since 2020.
            raise ArrError(
                f"Sonarr monitor toggle failed: {r.status_code} {r.text}"
            )

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


# ---------------- Jellyfin ----------------------------------------------------

class JellyfinClient:
    """Talks to a Jellyfin server. API-key auth via the MediaBrowser header."""

    def __init__(self, base_url: str, api_key: str, user_id: str = "",
                 timeout: float = 60.0):
        self.base = _normalize_url(base_url)
        self.api_key = api_key
        self.user_id = (user_id or "").strip()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        # Jellyfin accepts either header form; sending both is harmless.
        return {
            "Authorization": f'MediaBrowser Token="{self.api_key}"',
            "X-MediaBrowser-Token": self.api_key,
            "Accept": "application/json",
        }

    async def users(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        r = await client.get(f"{self.base}/Users", headers=self._headers())
        r.raise_for_status()
        return r.json() or []

    async def _items_for_user(self, user_id: str, item_types: str,
                              fields: str, client: httpx.AsyncClient
                              ) -> list[dict[str, Any]]:
        params = {
            "Recursive": "true",
            "IncludeItemTypes": item_types,
            "Fields": fields,
        }
        r = await client.get(
            f"{self.base}/Users/{user_id}/Items",
            headers=self._headers(),
            params=params,
        )
        r.raise_for_status()
        return (r.json() or {}).get("Items", []) or []

    @staticmethod
    def _provider_ids(item: dict[str, Any]) -> dict[str, str]:
        """Normalize Jellyfin's ProviderIds dict to lowercase keys we use."""
        ids = item.get("ProviderIds") or {}
        out: dict[str, str] = {}
        for k, v in ids.items():
            if not v:
                continue
            kl = k.lower()
            if kl == "tmdb":
                out["tmdb"] = str(v)
            elif kl == "tvdb":
                out["tvdb"] = str(v)
            elif kl == "imdb":
                out["imdb"] = str(v) if str(v).startswith("tt") else f"tt{v}"
        return out

    async def watched_index(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Same shape as PlexClient.watched_index() so the scanner can treat
        them interchangeably. Aggregates across all non-hidden, non-disabled
        users (any user has watched → counts as watched)."""
        idx: dict[str, dict[str, dict[str, Any]]] = {
            "movie": {"tmdb": {}, "tvdb": {}, "imdb": {}},
            "tv":    {"tmdb": {}, "tvdb": {}, "imdb": {}},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            if self.user_id:
                user_ids = [self.user_id]
            else:
                users = await self.users(c)
                user_ids = []
                for u in users:
                    pol = u.get("Policy") or {}
                    if pol.get("IsDisabled") or pol.get("IsHidden"):
                        continue
                    if u.get("Id"):
                        user_ids.append(u["Id"])
            for uid in user_ids:
                movies = await self._items_for_user(
                    uid, "Movie", "ProviderIds,UserData", c)
                shows = await self._items_for_user(
                    uid, "Series",
                    "ProviderIds,UserData,RecursiveItemCount,ChildCount", c)
                self._merge_movies(movies, idx["movie"])
                self._merge_shows(shows, idx["tv"])
        return idx

    @staticmethod
    def _merge_movies(items: list[dict[str, Any]],
                      bucket: dict[str, dict[str, Any]]) -> None:
        for it in items:
            ud = it.get("UserData") or {}
            played = bool(ud.get("Played"))
            view_count = int(ud.get("PlayCount") or 0)
            info_template = {
                "watched": 1 if played else 0,
                "view_count": view_count,
                "total_episodes": None,
                "last_viewed_at": ud.get("LastPlayedDate"),
                "rating_key": None,
                "jellyfin_item_id": str(it.get("Id") or ""),
            }
            ids = JellyfinClient._provider_ids(it)
            for src, val in ids.items():
                existing = bucket[src].get(val)
                if existing:
                    # Aggregate across users (OR-watched, max view_count)
                    existing["watched"] = max(existing["watched"], info_template["watched"])
                    existing["view_count"] = max(existing["view_count"], view_count)
                    if not existing.get("jellyfin_item_id"):
                        existing["jellyfin_item_id"] = info_template["jellyfin_item_id"]
                else:
                    bucket[src][val] = dict(info_template)

    @staticmethod
    def _merge_shows(items: list[dict[str, Any]],
                     bucket: dict[str, dict[str, Any]]) -> None:
        for it in items:
            ud = it.get("UserData") or {}
            total = int(it.get("RecursiveItemCount") or 0)
            unplayed = int(ud.get("UnplayedItemCount") or 0)
            played = max(0, total - unplayed) if total else int(ud.get("PlayCount") or 0)
            if total > 0 and played >= total:
                w = 1
            elif played > 0:
                w = 2
            else:
                w = 0
            info_template = {
                "watched": w,
                "view_count": played,
                "total_episodes": total or None,
                "last_viewed_at": ud.get("LastPlayedDate"),
                "rating_key": None,
                "jellyfin_item_id": str(it.get("Id") or ""),
            }
            ids = JellyfinClient._provider_ids(it)
            for src, val in ids.items():
                existing = bucket[src].get(val)
                if existing:
                    # Aggregate: an episode watched by ANY user counts. Take
                    # max watched-state and max played count.
                    cur_w = existing["watched"]
                    if w == 1 or cur_w == 1:
                        existing["watched"] = 1
                    elif w == 2 or cur_w == 2:
                        existing["watched"] = 2
                    existing["view_count"] = max(existing["view_count"], played)
                    if total and not existing.get("total_episodes"):
                        existing["total_episodes"] = total
                    if not existing.get("jellyfin_item_id"):
                        existing["jellyfin_item_id"] = info_template["jellyfin_item_id"]
                else:
                    bucket[src][val] = dict(info_template)

    async def show_episodes(self, series_id: str) -> list[dict[str, Any]]:
        """Aggregated per-episode watch state across users for one Jellyfin
        series. Returns list of {season, episode, watched, view_count}."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            user_ids: list[str]
            if self.user_id:
                user_ids = [self.user_id]
            else:
                user_ids = [
                    u["Id"] for u in await self.users(c)
                    if not (u.get("Policy") or {}).get("IsDisabled")
                    and not (u.get("Policy") or {}).get("IsHidden")
                    and u.get("Id")
                ]
            agg: dict[tuple[int, int], dict[str, Any]] = {}
            for uid in user_ids:
                params = {
                    "Recursive": "true",
                    "ParentId": series_id,
                    "IncludeItemTypes": "Episode",
                    "Fields": "UserData,IndexNumber,ParentIndexNumber",
                }
                r = await c.get(
                    f"{self.base}/Users/{uid}/Items",
                    headers=self._headers(),
                    params=params,
                )
                r.raise_for_status()
                for ep in (r.json() or {}).get("Items", []) or []:
                    season = ep.get("ParentIndexNumber")
                    episode = ep.get("IndexNumber")
                    if season is None or episode is None:
                        continue
                    ud = ep.get("UserData") or {}
                    played = bool(ud.get("Played"))
                    pc = int(ud.get("PlayCount") or 0)
                    key = (int(season), int(episode))
                    cur = agg.get(key)
                    if cur is None:
                        agg[key] = {"season": int(season), "episode": int(episode),
                                    "watched": played, "view_count": pc}
                    else:
                        cur["watched"] = cur["watched"] or played
                        cur["view_count"] = max(cur["view_count"], pc)
            return list(agg.values())

    async def ping(self) -> dict[str, Any]:
        # /System/Info (not /Public) requires auth, so a bad API key surfaces
        # as HTTP 401 instead of a misleading 200.
        return await _ping(f"{self.base}/System/Info", headers=self._headers())


# ---------------- Seerr (formerly Overseerr / Jellyseerr) --------------------

class SeerrClient:
    """Talks to Seerr (https://github.com/seerr-team/seerr) — the continuation
    of Overseerr / Jellyseerr after both were sunset. The API surface is
    unchanged, so this client also works against any legacy Overseerr or
    Jellyseerr install."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = _normalize_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    @staticmethod
    def _display_name(requester: dict[str, Any] | None) -> str:
        if not requester:
            return "unknown"
        return (requester.get("displayName")
                or requester.get("username")
                or requester.get("plexUsername")
                or requester.get("jellyfinUsername")
                or requester.get("email")
                or "unknown")

    async def request_index(self) -> dict[str, dict[str, list[str]]]:
        """Walk every request (any status, any user) and return
        {'movie': {tmdb_id: [requesters]}, 'tv': {tmdb_id: [requesters]}}.

        Robust against three observed response shapes:
          (a) {"results":[...], "pageInfo":{"results":N}}  — Overseerr/Jellyseerr
          (b) bare list [...]                              — older builds
          (c) missing pageInfo                             — some forks
        """
        out: dict[str, dict[str, list[str]]] = {"movie": {}, "tv": {}}
        page_size = 100
        skip = 0
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            while True:
                r = await c.get(
                    f"{self.base}/api/v1/request",
                    headers=self._headers(),
                    params={
                        "take": page_size, "skip": skip,
                        "sort": "added", "filter": "all",
                    },
                )
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    items = data
                    total: int | None = None
                elif isinstance(data, dict):
                    items = data.get("results") or []
                    page_info = data.get("pageInfo") or {}
                    total = int(page_info.get("results")) if page_info.get("results") is not None else None
                else:
                    items = []
                    total = 0

                for req in items:
                    media = req.get("media") or {}
                    media_type = media.get("mediaType") or req.get("type")
                    tmdb_id = media.get("tmdbId")
                    if not tmdb_id or media_type not in ("movie", "tv"):
                        continue
                    name = self._display_name(req.get("requestedBy"))
                    bucket = out["movie" if media_type == "movie" else "tv"]
                    key = str(tmdb_id)
                    if name not in bucket.setdefault(key, []):
                        bucket[key].append(name)

                if not items:
                    break
                skip += len(items)
                if total is not None and skip >= total:
                    break
                if len(items) < page_size:
                    # Last page (server returned fewer than asked for)
                    break
        return out

    async def ping(self) -> dict[str, Any]:
        # /api/v1/auth/me requires auth (validates the API key).
        # /api/v1/status is unauthenticated and would falsely report OK on a
        # bad key. Fall back to it only if auth/me 404s (older builds).
        res = await _ping(f"{self.base}/api/v1/auth/me", headers=self._headers())
        if res.get("ok") or res.get("status") in (401, 403):
            return res
        if res.get("status") == 404:
            return await _ping(f"{self.base}/api/v1/status",
                               headers=self._headers())
        return res
