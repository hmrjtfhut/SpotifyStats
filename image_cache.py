"""Fetches and caches album art and artist images to disk so the UI never
re-downloads the same picture twice and can show something instantly on the
next app launch.

Two kinds of images are handled:
  - Album art: comes from the currently-playing snapshot (WinRT thumbnail
    bytes, or a Spotify Web API album art URL) and is written straight to the
    database against the song row.
  - Artist images: only available through the Spotify Web API's
    `artist(id)['images']` field, so these only populate when a Spotify
    account is connected. Without an account, artist rows simply show the
    generic placeholder art.

Nothing here blocks the Tk main thread — every network fetch is expected to
be called from a background thread (the tracker's polling thread, or a short
lived worker thread kicked off by the UI).
"""
import hashlib
import os
import threading
from pathlib import Path

import requests


class ImageCache:
    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir or (Path.home() / ".spotistats" / "image_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem_cache = {}          # url -> bytes
        self._artist_id_cache = {}    # artist name -> spotify artist id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Generic URL -> bytes, with on-disk persistence
    # ------------------------------------------------------------------
    def get_bytes(self, url, timeout=6):
        if not url:
            return None

        with self._lock:
            if url in self._mem_cache:
                return self._mem_cache[url]

        disk_path = self._disk_path_for(url)
        if disk_path.exists():
            try:
                data = disk_path.read_bytes()
                with self._lock:
                    self._mem_cache[url] = data
                return data
            except Exception:
                pass

        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            print(f"[ImageCache] Fetch failed for {url}: {exc}")
            return None

        try:
            disk_path.write_bytes(data)
        except Exception as exc:
            print(f"[ImageCache] Disk write failed: {exc}")

        with self._lock:
            self._mem_cache[url] = data
        return data

    def _disk_path_for(self, url):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.img"

    # ------------------------------------------------------------------
    # Artist images via the Spotify Web API (only when an account is
    # connected — spotify_api is the SpotifyAPIController instance)
    # ------------------------------------------------------------------
    def get_artist_image_url(self, spotify_api, artist_name):
        """Look up (and cache) the Spotify artist image URL for a given
        artist name. Returns None if unavailable or no account connected."""
        if not spotify_api or not spotify_api.is_available():
            return None

        with self._lock:
            cached_id = self._artist_id_cache.get(artist_name)

        client = spotify_api._get_client(interactive=False)
        if client is None:
            return None

        try:
            if cached_id is None:
                results = client.search(q=artist_name, type="artist", limit=1)
                items = (results.get("artists") or {}).get("items") or []
                if not items:
                    return None
                artist_obj = items[0]
                with self._lock:
                    self._artist_id_cache[artist_name] = artist_obj["id"]
            else:
                artist_obj = client.artist(cached_id)

            images = artist_obj.get("images") or []
            return images[0]["url"] if images else None
        except Exception as exc:
            print(f"[ImageCache] Artist image lookup failed for {artist_name}: {exc}")
            return None

    def prefetch_artist_image_async(self, spotify_api, artist_name, db, on_done=None):
        """Fire-and-forget background fetch that writes the result straight
        into the database so it's cached for next time. Safe to call from
        the Tk main thread — the actual network work happens off-thread."""
        def worker():
            url = self.get_artist_image_url(spotify_api, artist_name)
            if url:
                try:
                    db.set_artist_image(artist_name, url)
                except Exception as exc:
                    print(f"[ImageCache] DB write failed for {artist_name}: {exc}")
            if on_done:
                try:
                    on_done(url)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Library backfill — proactively discover art for songs/artists that
    # are already in the database instead of only fetching a URL the moment
    # a song happens to be playing. Only useful when a Spotify account is
    # connected, since the Web API is the only source of search-by-name
    # lookups; without an account these are no-ops.
    # ------------------------------------------------------------------
    def backfill_missing_song_art(self, spotify_api, db, limit=500, on_progress=None):
        """Search the Web API for album art for every song in the library
        that doesn't have an album_art_url yet. Returns {"checked", "found"}."""
        if not spotify_api or not spotify_api.is_available():
            return {"checked": 0, "found": 0}

        client = spotify_api._get_client(interactive=False)
        if client is None:
            return {"checked": 0, "found": 0}

        try:
            songs = list(db.get_all_songs(limit=limit))
        except Exception as exc:
            print(f"[ImageCache] Could not list songs for backfill: {exc}")
            return {"checked": 0, "found": 0}

        checked = 0
        found = 0
        total_missing = sum(1 for row in songs if not self._row_get(row, 5))

        for row in songs:
            title = self._row_get(row, 0)
            artist = self._row_get(row, 1)
            art_url = self._row_get(row, 5)
            if art_url or not title or not artist:
                continue

            checked += 1
            try:
                results = client.search(q=f"track:{title} artist:{artist}", type="track", limit=1)
                items = (results.get("tracks") or {}).get("items") or []
                if not items:
                    # Fall back to a looser query without field filters —
                    # exact "track:"/"artist:" filters sometimes miss due to
                    # minor punctuation differences (e.g. "feat." vs "ft.").
                    results = client.search(q=f"{title} {artist}", type="track", limit=1)
                    items = (results.get("tracks") or {}).get("items") or []

                if items:
                    images = (items[0].get("album") or {}).get("images") or []
                    if images:
                        url = images[0]["url"]
                        db.set_song_art(title, artist, url)
                        found += 1
            except Exception as exc:
                print(f"[ImageCache] Song art backfill error for '{title}' by '{artist}': {exc}")

            if on_progress:
                try:
                    on_progress(checked, total_missing, found)
                except Exception:
                    pass

        return {"checked": checked, "found": found}

    def backfill_missing_artist_images(self, spotify_api, db, limit=300, on_progress=None):
        """Search the Web API for a photo for every artist in the library
        that doesn't have an image_url yet. Returns {"checked", "found"}."""
        if not spotify_api or not spotify_api.is_available():
            return {"checked": 0, "found": 0}

        try:
            artists = list(db.get_all_artists(limit=limit))
        except Exception as exc:
            print(f"[ImageCache] Could not list artists for backfill: {exc}")
            return {"checked": 0, "found": 0}

        checked = 0
        found = 0
        total_missing = sum(1 for row in artists if not self._row_get(row, 4))

        for row in artists:
            name = self._row_get(row, 0)
            image_url = self._row_get(row, 4)
            if image_url or not name:
                continue

            checked += 1
            url = self.get_artist_image_url(spotify_api, name)
            if url:
                db.set_artist_image(name, url)
                found += 1

            if on_progress:
                try:
                    on_progress(checked, total_missing, found)
                except Exception:
                    pass

        return {"checked": checked, "found": found}

    @staticmethod
    def _row_get(row, index):
        try:
            return row[index]
        except (IndexError, KeyError):
            return None
