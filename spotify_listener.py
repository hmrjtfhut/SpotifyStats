"""Single source of truth for "what's playing" and playback control.

Priority: Windows media session API (WinRT) for local Spotify → Spotify
Web API for phone/remote. Never both at the same time.
"""
import asyncio
from ctypes import cdll
import ctypes.wintypes as wintypes
import psutil
import requests
from spotify_api import SpotifyAPIController

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager)
    from winsdk.windows.media import MediaPlaybackAutoRepeatMode
    from winsdk.windows.storage.streams import Buffer, InputStreamOptions, DataReader
    HAS_WINSDK = True
except ImportError:
    HAS_WINSDK = False
    MediaPlaybackAutoRepeatMode = None

try:
    _user32 = cdll.LoadLibrary("user32")
    from ctypes import c_size_t
    _keybd_event = _user32.keybd_event
    _keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, c_size_t]
    _keybd_event.restype  = None
    HAS_USER32 = True
except Exception:
    HAS_USER32 = False

try:
    import win32gui as _win32gui
    HAS_WIN32GUI = True
except Exception:
    _win32gui    = None
    HAS_WIN32GUI = False

try:
    import win32process as _win32process
    HAS_WIN32PROCESS = True
except Exception:
    _win32process    = None
    HAS_WIN32PROCESS = False

# Spotify window title is "Track Name - Artist Name" when playing.
# These window class names cover the current Electron-based Spotify desktop app.
_SPOTIFY_WIN_CLASSES = {"SpotifyMainWindow", "Chrome_WidgetWin_0", "Chrome_WidgetWin_1"}
# Titles that appear when Spotify is idle / paused / showing an ad — never a real track.
_SPOTIFY_IDLE_TITLES = {
    "", "spotify", "spotify premium", "spotify free",
    "advertisement", "spotify - web player",
}

VK_MEDIA_NEXT_TRACK  = 0xB0
VK_MEDIA_PREV_TRACK  = 0xB1
VK_MEDIA_PLAY_PAUSE  = 0xB3
KEYEVENTF_KEYUP      = 0x0002

IDLE_SNAPSHOT = {
    "song": None, "artist": None, "album": None, "thumbnail": None,
    "is_playing": False, "position_sec": 0.0, "duration_sec": 0.0,
    "can_play_pause": False, "can_next": False, "can_previous": False,
    "can_shuffle": False, "can_repeat": False,
    "shuffle_active": False, "repeat_mode": "off",
    "source": "none", "connected": False,
}
_IGNORED_TRACKS   = {("up next", "dj x")}
_THUMB_CACHE_LIMIT = 30


class SpotifyListener:
    def __init__(self, callback=None):
        self.callback     = callback
        self.spotify_api  = SpotifyAPIController()
        self._thumb_cache = {}
        # Cached win32gui HWND for the Spotify window so we don't re-scan
        # all windows on every tick — just GetWindowText on the known handle.
        self._spotify_hwnd = None

    def is_spotify_running(self):
        try:
            for p in psutil.process_iter(["name"]):
                if "spotify" in (p.info.get("name") or "").lower():
                    return True
        except Exception: pass
        return False

    def get_snapshot(self):
        # Priority 1: Windows media session API (has position, art, full controls)
        if HAS_WINSDK:
            try:
                snap = asyncio.run(self._get_local_snapshot_async())
            except Exception as exc:
                print(f"[Listener] WinRT error: {exc}"); snap = None
            if snap is not None:
                return self._filter_ignored(snap)

        # Priority 2: Spotify window title (no account needed, no position/art,
        # but works when winsdk is missing/broken and Spotify desktop is open)
        win_snap = self._get_window_title_snapshot()
        if win_snap is not None:
            return self._filter_ignored(win_snap)

        # Priority 3: Spotify Web API (needs account; covers phone/remote playback)
        return self._filter_ignored(self._get_remote_snapshot())

    def _get_window_title_snapshot(self):
        """Read the currently playing track from the Spotify desktop window title.

        On first call (or after the Spotify window is destroyed) we scan all
        windows to find the Spotify HWND and cache it.  Every subsequent call
        reads the title directly from that HWND — no re-scan, no risk of
        accidentally reading another window.
        """
        if not HAS_WIN32GUI:
            return None

        # If we have a cached handle, verify it's still alive and read from it.
        if self._spotify_hwnd is not None:
            try:
                if _win32gui.IsWindow(self._spotify_hwnd):
                    title = _win32gui.GetWindowText(self._spotify_hwnd)
                    return self._parse_spotify_title(title)
                else:
                    # Window was destroyed (Spotify closed)
                    self._spotify_hwnd = None
            except Exception:
                self._spotify_hwnd = None

        # No valid cached handle — scan all windows once to find Spotify.
        # We verify each candidate window actually belongs to Spotify.exe via
        # its process ID so we can never accidentally latch onto another
        # Electron app (VS Code, Discord, etc.) that shares the same window
        # class names.
        spotify_pids = self._get_spotify_pids()

        if HAS_WIN32PROCESS and not spotify_pids:
            # win32process is available and reliable; an empty PID set means
            # Spotify.exe is simply not running — skip the scan entirely rather
            # than falling through to the class-name heuristic, which could
            # match VS Code, Discord, or any other Electron app.
            return None

        found_hwnd   = None
        found_title  = None

        def _enum_cb(hwnd, _):
            nonlocal found_hwnd, found_title
            if found_hwnd:
                return
            try:
                if not _win32gui.IsWindowVisible(hwnd):
                    return

                # ── PID check: reject any window not owned by Spotify.exe ──
                if HAS_WIN32PROCESS:
                    try:
                        _, pid = _win32process.GetWindowThreadProcessId(hwnd)
                        if pid not in spotify_pids:
                            return
                    except Exception:
                        return
                else:
                    # win32process unavailable: class-name heuristic only.
                    # Less reliable but better than nothing.
                    cls = _win32gui.GetClassName(hwnd)
                    if cls not in _SPOTIFY_WIN_CLASSES and "spotify" not in cls.lower():
                        return

                title = _win32gui.GetWindowText(hwnd)
                if not title:
                    return

                # Cache the handle whether or not a track is playing right now —
                # on the next tick it may start and we want to already be locked
                # onto the correct window.
                found_hwnd  = hwnd
                found_title = title
            except Exception:
                pass

        try:
            _win32gui.EnumWindows(_enum_cb, None)
        except Exception as exc:
            print(f"[Listener] win32gui enum error: {exc}")
            return None

        if found_hwnd:
            self._spotify_hwnd = found_hwnd
            return self._parse_spotify_title(found_title)

        return None

    @staticmethod
    def _get_spotify_pids():
        """Return the set of PIDs for all running Spotify.exe processes.
        We collect all of them because Spotify on Windows typically runs
        several helper processes under the same executable name and the
        main UI window may be owned by any one of them.
        """
        pids = set()
        try:
            for proc in psutil.process_iter(["name", "pid"]):
                name = (proc.info.get("name") or "").lower()
                if name in ("spotify.exe", "spotify"):
                    pids.add(proc.info["pid"])
        except Exception as exc:
            print(f"[Listener] psutil pid scan error: {exc}")
        return pids

    def _parse_spotify_title(self, title):
        """Parse a Spotify window title into a snapshot dict, or return None
        if the title indicates Spotify is idle/paused rather than playing."""
        if not title:
            return None

        # Strip trailing app-name suffixes some Spotify versions append
        for suffix in (" | Spotify", " - Spotify"):
            if title.lower().endswith(suffix.lower()):
                title = title[: -len(suffix)]

        # Idle / paused / ad states — not a real track
        if title.lower().strip() in _SPOTIFY_IDLE_TITLES or " - " not in title:
            return None

        # Format is "Track Name - Artist Name"; split at the FIRST " - " so
        # a track name containing " - " doesn't break the parse.
        parts  = title.split(" - ", 1)
        song   = parts[0].strip()
        artist = parts[1].strip()

        if not song or not artist:
            return None
        if song.lower() in _SPOTIFY_IDLE_TITLES or artist.lower() in _SPOTIFY_IDLE_TITLES:
            return None

        return {
            "song":           song,
            "artist":         artist,
            "album":          None,
            "thumbnail":      None,
            "is_playing":     True,   # title only shows track when actively playing
            "position_sec":   0.0,
            "duration_sec":   0.0,
            "can_play_pause": HAS_USER32,
            "can_next":       HAS_USER32,
            "can_previous":   HAS_USER32,
            "can_shuffle":    False,
            "can_repeat":     False,
            "shuffle_active": False,
            "repeat_mode":    "off",
            "source":         "window_title",
            "connected":      True,
        }

    @staticmethod
    def _filter_ignored(snap):
        key = ((snap.get("song") or "").strip().lower(),
               (snap.get("artist") or "").strip().lower())
        if key in _IGNORED_TRACKS:
            merged = dict(IDLE_SNAPSHOT)
            merged["connected"] = snap.get("connected", False)
            merged["source"]    = snap.get("source", "none")
            return merged
        return snap

    async def _get_local_snapshot_async(self):
        session = await self._find_spotify_session()
        if session is None: return None
        props        = await session.try_get_media_properties_async()
        playback_info = session.get_playback_info()
        controls     = playback_info.controls
        timeline     = session.get_timeline_properties()
        start_sec    = self._td_to_sec(getattr(timeline, "start_time", None))
        end_sec      = self._td_to_sec(getattr(timeline, "end_time",   None))
        position_sec = self._td_to_sec(getattr(timeline, "position",   None))
        song         = getattr(props, "title",  None) or None
        artist       = getattr(props, "artist", None) or None
        thumbnail    = await self._thumbnail_bytes(song, artist, getattr(props, "thumbnail", None))
        return {
            "song": song, "artist": artist,
            "album":       getattr(props, "album_title", None) or None,
            "thumbnail":   thumbnail,
            "is_playing":  self._winrt_is_playing(getattr(playback_info, "playback_status", None)),
            "position_sec": max(0.0, position_sec),
            "duration_sec": max(0.0, end_sec - start_sec),
            "can_play_pause": bool(
                getattr(controls, "is_play_pause_toggle_enabled", False)
                or getattr(controls, "is_play_enabled", False)
                or getattr(controls, "is_pause_enabled", False)),
            "can_next":     bool(getattr(controls, "is_next_enabled",     False)),
            "can_previous": bool(getattr(controls, "is_previous_enabled", False)),
            "can_shuffle":  bool(getattr(controls, "is_shuffle_enabled",  False)),
            "can_repeat":   bool(getattr(controls, "is_repeat_enabled",   False)),
            "shuffle_active": bool(getattr(playback_info, "is_shuffle_active", False)),
            "repeat_mode":  self._winrt_repeat(getattr(playback_info, "auto_repeat_mode", None)),
            "source": "local", "connected": True,
        }

    async def _find_spotify_session(self):
        try:
            manager  = await MediaManager.request_async()
            sessions = manager.get_sessions()
        except Exception: return None
        for s in sessions:
            try:
                app_id = (s.source_app_user_model_id or "").lower()
            except Exception: app_id = ""
            if "spotify" in app_id: return s
        return None

    def _get_remote_snapshot(self):
        if not self.spotify_api.is_available():
            return dict(IDLE_SNAPSHOT, connected=self.is_spotify_running())
        details = self.spotify_api.get_current_playback_details()
        if not details:
            return dict(IDLE_SNAPSHOT, connected=self.is_spotify_running())
        thumbnail = self._download_thumbnail(
            details.get("song"), details.get("artist"), details.get("album_art_url"))
        return {
            "song": details.get("song"), "artist": details.get("artist"),
            "album": None, "thumbnail": thumbnail,
            "is_playing":   details.get("is_playing", False),
            "position_sec": details.get("position_sec", 0.0),
            "duration_sec": details.get("duration_sec", 0.0),
            "can_play_pause": True, "can_next": True, "can_previous": True,
            "can_shuffle": True, "can_repeat": True,
            "shuffle_active": details.get("shuffle_active", False),
            "repeat_mode":    details.get("repeat_mode", "off"),
            "source": "remote", "connected": True,
        }

    def send_command(self, command):
        if HAS_WINSDK:
            try:
                if asyncio.run(self._send_local_command_async(command)): return True
            except Exception as exc:
                print(f"[Listener] Local command error ({command}): {exc}")
        if self.spotify_api.is_available() and self.spotify_api.has_cached_auth():
            if self._send_remote_command(command): return True
        return self._media_key_fallback(command)

    async def _send_local_command_async(self, command):
        session = await self._find_spotify_session()
        if session is None: return False
        pi = session.get_playback_info(); c = pi.controls
        is_playing = self._winrt_is_playing(getattr(pi, "playback_status", None))
        if command == "play_pause":
            if getattr(c, "is_play_pause_toggle_enabled", False):
                return bool(await session.try_toggle_play_pause_async())
            if is_playing and getattr(c, "is_pause_enabled", False):
                return bool(await session.try_pause_async())
            if getattr(c, "is_play_enabled", False):
                return bool(await session.try_play_async())
            return False
        if command == "next":
            return bool(await session.try_skip_next_async()) if getattr(c, "is_next_enabled", False) else False
        if command == "previous":
            return bool(await session.try_skip_previous_async()) if getattr(c, "is_previous_enabled", False) else False
        if command == "toggle_shuffle":
            if not getattr(c, "is_shuffle_enabled", False): return False
            return bool(await session.try_change_shuffle_active_async(
                not bool(getattr(pi, "is_shuffle_active", False))))
        if command == "cycle_repeat":
            if not getattr(c, "is_repeat_enabled", False) or MediaPlaybackAutoRepeatMode is None: return False
            current = self._winrt_repeat(getattr(pi, "auto_repeat_mode", None))
            target  = {"off": MediaPlaybackAutoRepeatMode.LIST,
                       "context": MediaPlaybackAutoRepeatMode.TRACK,
                       "track":   MediaPlaybackAutoRepeatMode.NONE}.get(current, MediaPlaybackAutoRepeatMode.NONE)
            return bool(await session.try_change_auto_repeat_mode_async(target))
        return False

    def _send_remote_command(self, command):
        return {"play_pause": self.spotify_api.play_pause,
                "next":       self.spotify_api.next_track,
                "previous":   self.spotify_api.previous_track,
                "toggle_shuffle": self.spotify_api.toggle_shuffle,
                "cycle_repeat":   self.spotify_api.cycle_repeat,
                }.get(command, lambda: False)()

    def _media_key_fallback(self, command):
        if not HAS_USER32: return False
        vk = {"play_pause": VK_MEDIA_PLAY_PAUSE,
               "next": VK_MEDIA_NEXT_TRACK,
               "previous": VK_MEDIA_PREV_TRACK}.get(command)
        if vk is None: return False
        try:
            _keybd_event(vk, 0, 0, 0); _keybd_event(vk, 0, KEYEVENTF_KEYUP, 0); return True
        except Exception as exc:
            print(f"[Listener] Media key error ({command}): {exc}"); return False

    def begin_spotify_auth(self):
        self.spotify_api.begin_auth()

    def forget_spotify_account(self):
        self.spotify_api.forget_account()

    @staticmethod
    def _td_to_sec(td):
        try: return float(td.total_seconds()) if td is not None else 0.0
        except Exception: return 0.0

    @staticmethod
    def _winrt_is_playing(status):
        try: return "playing" in str(status).lower()
        except Exception: return False

    @staticmethod
    def _winrt_repeat(mode):
        try: name = str(mode).split(".")[-1].lower()
        except Exception: name = "none"
        return {"none": "off", "list": "context", "track": "track"}.get(name, "off")

    def _cache_put(self, key, value):
        self._thumb_cache[key] = value
        if len(self._thumb_cache) > _THUMB_CACHE_LIMIT:
            self._thumb_cache.pop(next(iter(self._thumb_cache)), None)

    async def _thumbnail_bytes(self, song, artist, stream_ref):
        key = (song, artist)
        if key in self._thumb_cache: return self._thumb_cache[key]
        if stream_ref is None: self._cache_put(key, None); return None
        try:
            stream = await stream_ref.open_read_async()
            size   = stream.size
            if not size: self._cache_put(key, None); return None
            buf    = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            reader = DataReader.from_buffer(buf)
            data   = bytearray(size); reader.read_bytes(data)
            result = bytes(data)
        except Exception as exc:
            print(f"[Listener] Thumbnail error: {exc}"); result = None
        self._cache_put(key, result); return result

    def _download_thumbnail(self, song, artist, url):
        key = (song, artist)
        if key in self._thumb_cache: return self._thumb_cache[key]
        if not url: self._cache_put(key, None); return None
        try:
            resp = requests.get(url, timeout=5); resp.raise_for_status(); result = resp.content
        except Exception as exc:
            print(f"[Listener] Art download error: {exc}"); result = None
        self._cache_put(key, result); return result