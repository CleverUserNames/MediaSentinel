#!/usr/bin/env python3
"""
collect_recommendations.py  v2
1. Pulls ALL unique series and movies watched by all configured users
2. Gets TMDB recommendations for each watched item
3. Also runs genre-based TMDB Discover queries for broader coverage
4. Filters out anything already in library or already requested
5. Sends shortlist to local AI to rank and explain
6. Returns structured JSON for the report
"""

import json
import sys
import re
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# Shared session with connection pooling for TMDB calls
_tmdb_session = requests.Session()
_series_cache  = {}  # Cache Jellyfin series ID -> TMDB ID
_CACHE_FILE    = Path(__file__).parent.parent / "logs" / "tmdb_cache.json"
# Load persistent TMDB response cache from disk (covers details + recommendations)
try:
    _tmdb_cache = json.load(open(_CACHE_FILE, encoding="utf-8")) if _CACHE_FILE.exists() else {}
except Exception:
    _tmdb_cache = {}
_details_cache = _tmdb_cache  # alias for backward compat
_tmdb_session.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=20, pool_maxsize=20, max_retries=2
))

def safe_get(url, headers=None, timeout=10):
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


def jf_get(cfg, path, params=None):
    """Jellyfin GET -- builds URL as plain string to avoid encoding issues."""
    base = cfg["services"]["jellyfin"]["url"]
    h    = get_jellyfin_headers(cfg)
    if params:
        qs  = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{base}{path}?{qs}"
    else:
        url = f"{base}{path}"
    try:
        session = requests.Session()
        req     = requests.Request("GET", url, headers=h)
        prep    = session.prepare_request(req)
        prep.url = url  # override to prevent re-encoding
        r = session.send(prep, timeout=10)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


def get_jellyfin_headers(cfg):
    key = cfg["services"]["jellyfin"]["api_key"]
    return {"X-Emby-Authorization": f'MediaBrowser Token="{key}"'}


def get_tmdb_headers(cfg):
    token = cfg["services"]["tmdb"]["api_read_token"]
    return {"Authorization": f"Bearer {token}", "accept": "application/json"}


# -- Jellyfin history ---------------------------------------------------------

def get_all_unique_series(cfg, user_id, user_name):
    """Pull all unique series watched by querying series-level items directly."""
    items = []

    # 1. Get all unique series the user has played episodes from
    #    by querying Series type with IsPlayed filter
    data, err = jf_get(cfg, f"/Users/{user_id}/Items", {
        "IncludeItemTypes": "Series",
        "Recursive":        "true",
        "Fields":           "ProviderIds,Genres,UserData",
        "Filters":          "IsPlayed",
        "Limit":            200,
    })

    if not err and data:
        for item in data.get("Items", []):
            ids  = item.get("ProviderIds", {}) or {}
            tmdb = ids.get("Tmdb")
            if tmdb:
                items.append({
                    "title":      item.get("Name", "Unknown"),
                    "tmdb_id":    str(tmdb),
                    "media_type": "tv",
                    "genres":     item.get("Genres", []),
                    "user":       user_name,
                })

    # 2. Also get series where user has played some (not all) episodes
    #    using episode history and unique SeriesId lookup
    ep_data, ep_err = jf_get(cfg, f"/Users/{user_id}/Items", {
        "Filters":          "IsPlayed",
        "IncludeItemTypes": "Episode",
        "Recursive":        "true",
        "Fields":           "SeriesName,SeriesId",
        "SortBy":           "DatePlayed",
        "SortOrder":        "Descending",
        "Limit":            500,
    })

    seen_series_ids   = {i["tmdb_id"] for i in items}
    seen_jellyfin_ids = set()
    pending_sids      = []

    if not ep_err and ep_data:
        for ep in ep_data.get("Items", []):
            sid = ep.get("SeriesId")
            if not sid or sid in seen_jellyfin_ids:
                continue
            seen_jellyfin_ids.add(sid)
            pending_sids.append((sid, ep.get("SeriesName", "Unknown")))

    def lookup_series(sid_name):
        sid, name = sid_name
        if sid in _series_cache:
            cached = _series_cache[sid]
            if cached:
                return {**cached, "user": user_name}
            return None
        s_data, s_err = jf_get(cfg, f"/Users/{user_id}/Items/{sid}", {"Fields": "ProviderIds,Genres"})
        if s_err or not s_data:
            _series_cache[sid] = None
            return None
        ids  = s_data.get("ProviderIds", {}) or {}
        tmdb = ids.get("Tmdb")
        if not tmdb:
            _series_cache[sid] = None
            return None
        result = {
            "title":      name or s_data.get("Name", "Unknown"),
            "tmdb_id":    str(tmdb),
            "media_type": "tv",
            "genres":     s_data.get("Genres", []),
            "user":       user_name,
        }
        _series_cache[sid] = result
        return result

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(lookup_series, sn): sn for sn in pending_sids}
        for future in as_completed(futures):
            result = future.result()
            if result and result["tmdb_id"] not in seen_series_ids:
                seen_series_ids.add(result["tmdb_id"])
                items.append(result)

    print(f"  {user_name}: {len(items)} unique TV series watched", file=sys.stderr)
    return items


def get_watched_movies(cfg, user_id, user_name):
    """Pull all movies the user has played."""
    """Pull all movies the user has played or significantly watched."""
    items = []
    seen_tmdb = set()

    # Pass 1: fully played movies
    data, err = jf_get(cfg, f"/Users/{user_id}/Items", {
        "Filters":          "IsPlayed",
        "IncludeItemTypes": "Movie",
        "Recursive":        "true",
        "Fields":           "ProviderIds,Genres,UserData",
        "SortBy":           "DatePlayed",
        "SortOrder":        "Descending",
        "Limit":            200,
    })
    for item in (data or {}).get("Items", []) if not err else []:
        ids  = item.get("ProviderIds", {}) or {}
        tmdb = ids.get("Tmdb")
        if not tmdb or tmdb in seen_tmdb:
            continue
        seen_tmdb.add(tmdb)
        items.append({
            "title":      item.get("Name", "Unknown"),
            "tmdb_id":    str(tmdb),
            "media_type": "movie",
            "genres":     item.get("Genres", []),
            "user":       user_name,
        })

    # Pass 2: movies with play count > 0 or significant playback position (>20 min)
    data2, err2 = jf_get(cfg, f"/Users/{user_id}/Items", {
        "IncludeItemTypes": "Movie",
        "Recursive":        "true",
        "Fields":           "ProviderIds,Genres,UserData",
        "Filters":          "IsUnplayed",
        "Limit":            500,
    })
    TWENTY_MIN_TICKS = 20 * 60 * 10000000
    for item in (data2 or {}).get("Items", []) if not err2 else []:
        ud   = item.get("UserData", {}) or {}
        ids  = item.get("ProviderIds", {}) or {}
        tmdb = ids.get("Tmdb")
        play_count = ud.get("PlayCount", 0) or 0
        position   = ud.get("PlaybackPositionTicks", 0) or 0
        if not tmdb or tmdb in seen_tmdb:
            continue
        # Include if played at least once or watched more than 20 minutes
        if play_count > 0 or position >= TWENTY_MIN_TICKS:
            seen_tmdb.add(tmdb)
            items.append({
                "title":      item.get("Name", "Unknown"),
                "tmdb_id":    str(tmdb),
                "media_type": "movie",
                "genres":     item.get("Genres", []),
                "user":       user_name,
            })

    print("  " + user_name + ": " + str(len(items)) + " movies watched", file=sys.stderr)
    return items


# -- Library / Jellyseer exclusions -------------------------------------------

def get_library_tmdb_ids(cfg):
    """Get TMDB IDs for all items in the Jellyfin library.
    Uses direct requests session to avoid URL encoding issues with jf_get."""
    base = cfg["services"]["jellyfin"]["url"]
    h    = get_jellyfin_headers(cfg)
    ids  = set()

    for media_type in ["Movie", "Series"]:
        url = (f"{base}/Items?IncludeItemTypes={media_type}"
               f"&Recursive=true&Fields=ProviderIds&Limit=10000&HasTmdbId=true")
        try:
            session = requests.Session()
            req  = requests.Request("GET", url, headers=h)
            prep = session.prepare_request(req)
            prep.url = url  # override to prevent encoding
            r = session.send(prep, timeout=30)
            r.raise_for_status()
            for item in r.json().get("Items", []):
                tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
                if tmdb:
                    ids.add(str(tmdb))
        except Exception as e:
            print(f"  Warning: library scan failed for {media_type}: {e}", file=sys.stderr)

    return ids


def get_jellyseer_requested_ids(cfg):
    base = cfg["services"]["jellyseer"]["url"]
    key  = cfg["services"]["jellyseer"]["api_key"]
    h    = {"X-Api-Key": key}
    ids  = set()
    data, err = safe_get(f"{base}/api/v1/request?take=500&skip=0&sort=added", headers=h)
    if err or not data:
        return ids
    for r in data.get("results", []):
        tmdb = (r.get("media") or {}).get("tmdbId")
        if tmdb:
            ids.add(str(tmdb))
    return ids


# -- TMDB ---------------------------------------------------------------------

def tmdb_get(cfg, path, params=None):
    # Build cache key from path + sorted params
    p = {"language": "en-US", **(params or {})}
    cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]
    h = get_tmdb_headers(cfg)
    try:
        r = _tmdb_session.get(
            f"https://api.themoviedb.org/3{path}",
            headers=h, params=p, timeout=10
        )
        r.raise_for_status()
        result = r.json()
        _tmdb_cache[cache_key] = result
        return result
    except Exception:
        return None


def get_tmdb_recommendations(cfg, tmdb_id, media_type, min_rating=6.5):
    """Get TMDB similar/recommended titles for a watched item."""
    results = []
    for endpoint in [f"/{media_type}/{tmdb_id}/recommendations",
                     f"/{media_type}/{tmdb_id}/similar"]:
        data = tmdb_get(cfg, endpoint)
        if not data:
            continue
        for item in data.get("results", [])[:8]:
            if item.get("vote_average", 0) < min_rating:
                continue
            if item.get("vote_count", 0) < 50:
                continue
            year_str = (item.get("release_date") or item.get("first_air_date") or "")[:4]
            if year_str and int(year_str) < 2019:
                continue
            title = item.get("title") or item.get("name") or "Unknown"
            results.append({
                "tmdb_id":    str(item.get("id")),
                "title":      title,
                "media_type": media_type,
                "year":       (item.get("release_date") or item.get("first_air_date") or "")[:4],
                "rating":     round(item.get("vote_average", 0), 1),
                "overview":   (item.get("overview") or "")[:200],
                "genres":     [],
            })
        if results:
            break
    return results


# TMDB genre ID maps
MOVIE_GENRE_IDS = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Fantasy": 14, "Horror": 27, "Mystery": 9648, "Romance": 10749,
    "Science Fiction": 878, "Thriller": 53, "War": 10752,
}
TV_GENRE_IDS = {
    "Action & Adventure": 10759, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Kids": 10762, "Mystery": 9648, "Reality": 10764,
    "Sci-Fi & Fantasy": 10765, "Thriller": 9648,
}


def get_genre_based_recommendations(cfg, genres, media_type, exclude_ids, min_rating=7.0):
    """Use TMDB Discover to find highly rated titles in the user's top genres."""
    genre_map = TV_GENRE_IDS if media_type == "tv" else MOVIE_GENRE_IDS
    results   = []

    for genre in genres[:4]:
        genre_id = genre_map.get(genre)
        if not genre_id:
            continue
        endpoint = "/discover/tv" if media_type == "tv" else "/discover/movie"
        params = {
            "with_genres":      genre_id,
            "sort_by":          "popularity.desc",
            "vote_count.gte":   100,
            "vote_average.gte": min_rating,
            "page":             1,
        }
        if media_type == "tv":
            params["first_air_date.gte"] = "2021-01-01"
        else:
            params["primary_release_date.gte"] = "2021-01-01"
        data = tmdb_get(cfg, endpoint, params)
        if not data:
            continue
        for item in data.get("results", [])[:6]:
            tid = str(item.get("id"))
            if tid in exclude_ids:
                continue
            title = item.get("title") or item.get("name") or "Unknown"
            results.append({
                "tmdb_id":    tid,
                "title":      title,
                "media_type": media_type,
                "year":       (item.get("release_date") or item.get("first_air_date") or "")[:4],
                "rating":     round(item.get("vote_average", 0), 1),
                "overview":   (item.get("overview") or "")[:200],
                "genres":     [],
                "because":    f"{genre} genre discovery",
                "watched_by": "genre match",
            })

    return results


def get_tmdb_details(cfg, tmdb_id, media_type):
    data = tmdb_get(cfg, f"/{media_type}/{tmdb_id}")
    if not data:
        return {}
    poster = data.get("poster_path") or ""
    result = {
        "title":       data.get("title") or data.get("name") or "Unknown",
        "year":        (data.get("release_date") or data.get("first_air_date") or "")[:4],
        "rating":      round(data.get("vote_average", 0), 1),
        "overview":    (data.get("overview") or "")[:250],
        "genres":      [g.get("name", "") for g in (data.get("genres") or [])],
        "tmdb_url":    f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
        "poster_path": poster,
        "poster_url":  f"https://image.tmdb.org/t/p/w300{poster}" if poster else "",
    }
    return result


def get_top_actors(cfg, watched_items, top_n=10):
    """Pull cast for all watched items in parallel and return top actors."""
    actor_counts = defaultdict(int)
    actor_names  = {}
    lock_data    = []

    def fetch_credits(item):
        return tmdb_get(cfg, "/" + item["media_type"] + "/" + item["tmdb_id"] + "/credits")

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_credits, item): item for item in watched_items[:20]}
        for future in as_completed(futures):
            data = future.result()
            if not data:
                continue
            for person in data.get("cast", [])[:15]:
                pid  = person.get("id")
                name = person.get("name")
                if pid and name:
                    actor_counts[pid] += 1
                    actor_names[pid]   = name

    top = sorted(
        [(pid, actor_names[pid], count) for pid, count in actor_counts.items()],
        key=lambda x: -x[2]
    )[:top_n]
    return top





def get_actor_based_recommendations(cfg, top_actors, exclude_ids, media_type="tv", min_rating=7.0):
    """
    For each top actor, use TMDB discover to find their other highly-rated work
    that isn't already in the library.
    """
    results = []
    seen    = set(exclude_ids)

    for actor_id, actor_name, count in top_actors[:5]:
        endpoint = "/discover/tv" if media_type == "tv" else "/discover/movie"
        params = {
            "with_cast":        actor_id,
            "sort_by":          "popularity.desc",
            "vote_count.gte":   50,
            "vote_average.gte": min_rating,
            "page":             1,
        }
        if media_type == "tv":
            params["first_air_date.gte"] = "2018-01-01"
        else:
            params["primary_release_date.gte"] = "2018-01-01"
        data = tmdb_get(cfg, endpoint, params)
        if not data:
            continue
        for item in data.get("results", [])[:5]:
            tid = str(item.get("id"))
            if tid in seen:
                continue
            seen.add(tid)
            title = item.get("title") or item.get("name") or "Unknown"
            results.append({
                "tmdb_id":    tid,
                "title":      title,
                "media_type": media_type,
                "year":       (item.get("release_date") or item.get("first_air_date") or "")[:4],
                "rating":     round(item.get("vote_average", 0), 1),
                "overview":   (item.get("overview") or "")[:200],
                "genres":     [],
                "because":    "Features " + actor_name + " (seen in " + str(count) + " of your shows)",
                "watched_by": "actor match",
            })

    return results


# -- AI ranking ---------------------------------------------------------------

def rank_with_ai(cfg, suggestions, watch_summary):
    host  = cfg["server"]["local_ai_host"]
    model = cfg["server"]["local_ai_model"]

    numbered = []
    for i, s in enumerate(suggestions[:50]):
        mt  = s["media_type"].upper()
        ttl = s["title"]
        yr  = s["year"]
        rat = s["rating"]
        bec = s.get("because", "?")
        ov  = s.get("overview", "")[:100]
        numbered.append(str(i+1) + ". [" + mt + "] " + ttl + " (" + yr + ") Rating:" + str(rat) + " | Because:" + bec + " | " + ov)
    candidates_text = chr(10).join(numbered)

    system_prompt = (
        "You are a media recommendation assistant. "
        "You have a numbered list of candidate titles the user does not have. "
        "Select the 15 best matches based on their watch history. "
        "Return ONLY a JSON array. No markdown, no extra text. "
        'Format: [{"num": 1, "reason": "one sentence why"}, ...]'
        " Only use numbers 1 to " + str(len(suggestions[:50])) + " from the list. Do not invent entries."
    )

    user_prompt = (
        "WATCH HISTORY:" + chr(10) + watch_summary + chr(10) + chr(10) +
        "CANDIDATES:" + chr(10) + candidates_text + chr(10) + chr(10) +
        "Return JSON array with num and reason fields only."
    )

    try:
        r = requests.post(
            f"{host}/v1/chat/completions",
            json={
                "model":       model,
                "messages":    [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens":  800,
                "stream":      False,
            },
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^\\$",          "", raw, flags=re.MULTILINE)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        picks = json.loads(match.group(0) if match else raw)

        results = []
        seen_nums = set()
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            num = pick.get("num")
            if num is None or num in seen_nums:
                continue
            seen_nums.add(num)
            idx = int(num) - 1
            if 0 <= idx < len(suggestions):
                candidate = suggestions[idx].copy()
                candidate["reason"] = pick.get("reason", "")
                results.append(candidate)
        # Clean up reason field - if AI just returned the show name, build a better reason
        for item in results:
            reason = item.get("reason", "")
            because = item.get("because", "")
            if not reason or reason.strip() == because.strip() or len(reason) < 15:
                item["reason"] = "Recommended based on your watch history (" + because + ")"
        return results

    except Exception as e:
        print("  AI ranking failed (" + str(e) + "), falling back to rating sort", file=sys.stderr)
        return sorted(suggestions[:15], key=lambda x: -x.get("rating", 0))


def main():
    cfg     = load_config()
    rec_cfg = cfg.get("recommendations", {})
    users   = rec_cfg.get("jellyfin_users", [
        {"name": "Server", "id": rec_cfg.get("jellyfin_user_id", "")}
    ])
    min_rating = rec_cfg.get("min_tmdb_rating", 6.5)

    print("[Recommendations] Collecting watch history...", file=sys.stderr)

    all_watched      = []
    watch_by_user    = defaultdict(list)
    seen_tmdb        = set()
    genre_counts     = defaultdict(int)

    for user in users:
        tv_items    = get_all_unique_series(cfg, user["id"], user["name"])
        movie_items = get_watched_movies(cfg, user["id"], user["name"])

        for item in tv_items + movie_items:
            watch_by_user[user["name"]].append(item["title"])
            for g in item.get("genres", []):
                genre_counts[g] += 1
            if item["tmdb_id"] not in seen_tmdb:
                seen_tmdb.add(item["tmdb_id"])
                all_watched.append(item)

    print(f"  Total unique watched items: {len(all_watched)}", file=sys.stderr)

    # Top genres across all users
    top_genres = [g for g, _ in sorted(genre_counts.items(), key=lambda x: -x[1])[:8]]
    print(f"  Top genres: {top_genres}", file=sys.stderr)

    print("[Recommendations] Getting library/request exclusions...", file=sys.stderr)
    library_ids   = get_library_tmdb_ids(cfg)
    requested_ids = get_jellyseer_requested_ids(cfg)
    exclude_ids   = library_ids | requested_ids | seen_tmdb
    print(f"  Excluding {len(exclude_ids)} IDs already in library or requested", file=sys.stderr)

    print("[Recommendations] Fetching TMDB recommendations...", file=sys.stderr)
    candidates      = []
    seen_candidates = set()

    pending_candidates = []  # collect first, fetch details in parallel

    def add_candidate(rec, because, watched_by):
        tid = rec["tmdb_id"]
        if tid in exclude_ids or tid in seen_candidates:
            return
        seen_candidates.add(tid)
        pending_candidates.append({
            **rec,
            "because":    because,
            "watched_by": watched_by,
            "tmdb_url":   f"https://www.themoviedb.org/{rec['media_type']}/{tid}",
        })

    def fetch_details(item):
        # Skip detail fetch if we already have genres and overview populated
        if item.get("genres") and item.get("overview") and item.get("poster_url"):
            return item
        details = get_tmdb_details(cfg, item["tmdb_id"], item["media_type"])
        return {**item, **{k: v for k, v in details.items() if v}}

    # 1. Item-based recommendations - parallel fetch
    def fetch_item_recs(item):
        recs = get_tmdb_recommendations(cfg, item["tmdb_id"], item["media_type"], min_rating)
        return [(rec, item["title"], item["user"]) for rec in recs[:4]]

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(fetch_item_recs, item) for item in all_watched[:25]]
        for future in as_completed(futures):
            for rec, because, watched_by in future.result():
                add_candidate(rec, because, watched_by)

    # 2. Genre-based discovery for TV
    tv_genres = [g for g, _ in sorted(
        {g: genre_counts[g] for g in genre_counts if g in TV_GENRE_IDS}.items(),
        key=lambda x: -x[1]
    )[:5]]
    if tv_genres:
        genre_recs = get_genre_based_recommendations(cfg, tv_genres, "tv", exclude_ids, min_rating=7.0)
        for rec in genre_recs:
            add_candidate(rec, rec.get("because", "genre match"), rec.get("watched_by", ""))

    # 3. Genre-based discovery for movies
    movie_genres = [g for g, _ in sorted(
        {g: genre_counts[g] for g in genre_counts if g in MOVIE_GENRE_IDS}.items(),
        key=lambda x: -x[1]
    )[:5]]
    if movie_genres:
        genre_recs = get_genre_based_recommendations(cfg, movie_genres, "movie", exclude_ids, min_rating=7.0)
        for rec in genre_recs:
            add_candidate(rec, rec.get("because", "genre match"), rec.get("watched_by", ""))

    # 4. Actor-based discovery
    print("  Analyzing actor appearances across watch history...", file=sys.stderr)
    top_actors = get_top_actors(cfg, all_watched, top_n=10)
    if top_actors:
        actor_list = ", ".join(n + "(" + str(c) + " shows)" for _, n, c in top_actors[:5])
        print("  Top actors: " + actor_list, file=sys.stderr)
        for mt in ["tv", "movie"]:
            actor_recs = get_actor_based_recommendations(cfg, top_actors, exclude_ids, media_type=mt, min_rating=7.0)
            for rec in actor_recs:
                add_candidate(rec, rec.get("because", "actor match"), rec.get("watched_by", "actor match"))
    else:
        print("  No recurring actors found across watched items", file=sys.stderr)

    # Cap to top 30 by rating before detail fetch - we only show 15 in report
    top_pending = sorted(pending_candidates, key=lambda x: -x.get("rating", 0))[:30]
    print("  Fetching details for " + str(len(top_pending)) + " top candidates (of " + str(len(pending_candidates)) + " total)...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_details, item): item for item in top_pending}
        for future in as_completed(futures):
            try:
                candidates.append(future.result())
            except Exception:
                candidates.append(futures[future])

    print("  Found " + str(len(candidates)) + " candidate recommendations", file=sys.stderr)

    if not candidates:
        print(json.dumps({
            "status":          "no_candidates",
            "message":         "No new recommendations found",
            "recommendations": [],
            "watch_summary":   {},
            "top_genres":      top_genres,
        }))
        return

    # Build watch summary for AI including actor data
    watch_lines = []
    for uname, titles in watch_by_user.items():
        watch_lines.append(uname + " watched: " + ", ".join(titles[:20]))
    watch_lines.append("Top genres: " + ", ".join(top_genres))
    if top_actors:
        actor_summary = ", ".join(n + " (" + str(c) + " shows)" for _, n, c in top_actors[:8])
        watch_lines.append("Frequently appearing actors: " + actor_summary)
    watch_summary = chr(10).join(watch_lines)

    print("[Recommendations] Sending to AI for ranking...", file=sys.stderr)
    ranked = rank_with_ai(cfg, candidates, watch_summary)

    # Merge AI picks with candidate details
    candidate_map = {c["tmdb_id"]: c for c in candidates}
    final = []
    for item in ranked:
        if not isinstance(item, dict) or "error" in item:
            continue
        tid  = str(item.get("tmdb_id", ""))
        base = candidate_map.get(tid, {})
        final.append({
            "tmdb_id":      tid,
            "title":        item.get("title")      or base.get("title", "Unknown"),
            "media_type":   item.get("media_type") or base.get("media_type", "movie"),
            "year":         item.get("year")        or base.get("year", ""),
            "rating":       item.get("rating")      or base.get("rating", 0),
            "reason":       item.get("reason", ""),
            "suggested_by": item.get("suggested_by") or f"Based on: {base.get('because','')}",
            "genres":       base.get("genres", []),
            "overview":     base.get("overview", ""),
            "tmdb_url":     base.get("tmdb_url", ""),
            "watched_by":   base.get("watched_by", ""),
            "poster_path":  base.get("poster_path", ""),
            "poster_url":   base.get("poster_url", ""),
        })

    # Save details cache to disk for next run
    try:
        json.dump(_tmdb_cache, open(_CACHE_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass

    print(json.dumps({
        "status":           "ok",
        "recommendations":  final,
        "watch_summary":    {u: t[:15] for u, t in watch_by_user.items()},
        "candidates_found": len(candidates),
        "top_genres":       top_genres,
    }, indent=2))


if __name__ == "__main__":
    main()
