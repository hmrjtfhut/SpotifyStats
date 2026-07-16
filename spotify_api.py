"""Thin wrapper around the Spotify Web API.

Only used as a *fallback* for when there is no local Spotify desktop session
for winsdk to read (e.g. phone/remote device). Override credentials by editing
~/.spotistats/spotify_app.json or via SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET
/ SPOTIPY_REDIRECT_URI env vars.
"""
import json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import requests

try:
    import spotipy
    HAS_SPOTIPY = True
except Exception:
    spotipy = None
    HAS_SPOTIPY = False

_FALLBACK_CLIENT_ID     = "c3fc61202c5e44d28f2f95bcd4bcb147"
_FALLBACK_CLIENT_SECRET = "TO RUN THIS USE YOUR OWN OR USE THE EXE"
_FALLBACK_REDIRECT_URI  = "http://127.0.0.1:8888/callback"
DEFAULT_SCOPE   = "user-read-playback-state user-modify-playback-state"
AUTHORIZE_URL   = "https://accounts.spotify.com/authorize"
TOKEN_URL       = "https://accounts.spotify.com/api/token"


def _load_app_credentials():
    config_path = Path.home() / ".spotistats" / "spotify_app.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    file_values = {}
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as fh:
                file_values = json.load(fh) or {}
        except Exception as exc:
            print(f"[SpotifyAPI] Could not read {config_path}: {exc}")
    else:
        try:
            with config_path.open("w", encoding="utf-8") as fh:
                json.dump({"client_id": _FALLBACK_CLIENT_ID,
                           "client_secret": _FALLBACK_CLIENT_SECRET,
                           "redirect_uri": _FALLBACK_REDIRECT_URI}, fh, indent=2)
        except Exception as exc:
            print(f"[SpotifyAPI] Could not write {config_path}: {exc}")
    return (
        os.getenv("SPOTIPY_CLIENT_ID")     or file_values.get("client_id")     or _FALLBACK_CLIENT_ID,
        os.getenv("SPOTIPY_CLIENT_SECRET") or file_values.get("client_secret") or _FALLBACK_CLIENT_SECRET,
        os.getenv("SPOTIPY_REDIRECT_URI")  or file_values.get("redirect_uri")  or _FALLBACK_REDIRECT_URI,
    )


class SpotifyAPIController:
    def __init__(self):
        self.enabled = HAS_SPOTIPY
        self.client  = None
        self.client_id, self.client_secret, self.redirect_uri = _load_app_credentials()
        self.cache_dir  = Path.home() / ".spotistats"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = self.cache_dir / "spotify_token.json"
        self.auth_thread     = None
        self.auth_in_progress = False
        self.auth_state      = None
        self.lock            = threading.Lock()
        self._cache_ttl          = float(os.getenv("SPOTI_CACHE_TTL", "2.0"))
        self._min_interval       = float(os.getenv("SPOTI_MIN_INTERVAL", "0.8"))
        self._last_request       = 0.0
        self._last_playback      = None
        self._last_playback_ts   = 0.0
        self._rate_limited_until = 0.0

    def is_available(self):
        return self.enabled

    def has_cached_auth(self):
        token = self._load_token()
        return token is not None and self._ensure_valid_token(token, interactive=False) is not None

    def get_current_playback_details(self):
        now = time.time()
        if now < self._rate_limited_until:
            return self._last_playback
        if self._last_playback and (now - self._last_playback_ts) < self._cache_ttl:
            return self._last_playback
        client = self._get_client(interactive=False)
        if client is None:
            return None
        if now - self._last_request < self._min_interval:
            return self._last_playback
        try:
            self._last_request = now
            playback = client.current_playback()
            if not playback:
                self._last_playback = None
                self._last_playback_ts = now
                return None
            item    = playback.get("item") or {}
            artists = item.get("artists") or []
            artist_name = ", ".join(a.get("name") for a in artists if a.get("name")) or None
            images  = (item.get("album") or {}).get("images") or []
            art_url = images[0]["url"] if images else None
            details = {
                "song": item.get("name"), "artist": artist_name,
                "is_playing":   bool(playback.get("is_playing", False)),
                "position_sec": max(0.0, int(playback.get("progress_ms") or 0) / 1000.0),
                "duration_sec": max(0.0, int(item.get("duration_ms") or 0) / 1000.0),
                "album_art_url": art_url,
                "device_id":     (playback.get("device") or {}).get("id"),
                "shuffle_active": bool(playback.get("shuffle_state", False)),
                "repeat_mode":    playback.get("repeat_state", "off"),
            }
            self._last_playback    = details
            self._last_playback_ts = now
            return details
        except Exception as exc:
            return self._handle_api_error(exc, fallback=self._last_playback)

    def _active_device_id(self):
        return self._last_playback.get("device_id") if self._last_playback else None

    def play_pause(self):
        client = self._get_client(interactive=True)
        if client is None: return False
        try:
            details = self.get_current_playback_details()
            if details and details.get("is_playing"):
                client.pause_playback(device_id=self._active_device_id())
            else:
                client.start_playback(device_id=self._active_device_id())
            self._last_playback_ts = 0.0
            return True
        except Exception as exc:
            print(f"[SpotifyAPI] play_pause error: {exc}"); return False

    def next_track(self):
        return self._simple_control(lambda c: c.next_track(device_id=self._active_device_id()))

    def previous_track(self):
        return self._simple_control(lambda c: c.previous_track(device_id=self._active_device_id()))

    def _simple_control(self, fn):
        client = self._get_client(interactive=True)
        if client is None: return False
        try:
            fn(client); self._last_playback_ts = 0.0; return True
        except Exception as exc:
            print(f"[SpotifyAPI] control error: {exc}"); return False

    def toggle_shuffle(self):
        client = self._get_client(interactive=True)
        if client is None: return False
        try:
            details = self.get_current_playback_details()
            if not details: return False
            client.shuffle(not bool(details.get("shuffle_active", False)), device_id=self._active_device_id())
            self._last_playback_ts = 0.0; return True
        except Exception as exc:
            print(f"[SpotifyAPI] Shuffle error: {exc}"); return False

    def cycle_repeat(self):
        client = self._get_client(interactive=True)
        if client is None: return False
        try:
            details = self.get_current_playback_details()
            if not details: return False
            next_mode = {"off": "context", "context": "track", "track": "off"}.get(details.get("repeat_mode","off"),"off")
            client.repeat(next_mode, device_id=self._active_device_id())
            self._last_playback_ts = 0.0; return True
        except Exception as exc:
            print(f"[SpotifyAPI] Repeat error: {exc}"); return False

    def _handle_api_error(self, exc, fallback):
        s = str(exc).lower()
        if "429" in s or "rate" in s:
            retry = 5
            try:
                headers = getattr(exc, "headers", None) or getattr(getattr(exc, "response", None), "headers", None)
                if headers:
                    ra = headers.get("Retry-After") or headers.get("retry-after")
                    if ra: retry = int(ra)
            except Exception: pass
            self._rate_limited_until = time.time() + retry
            print(f"[SpotifyAPI] Rate limited; backing off for {retry}s")
            return fallback
        print(f"[SpotifyAPI] Request error: {exc}"); return None

    def _get_client(self, interactive):
        if not self.enabled: return None
        if self.client is not None: return self.client
        token_info = self._ensure_valid_token(self._load_token(), interactive=False)
        if token_info is None:
            if interactive: self._start_auth_flow()
            return None
        try:
            self.client = spotipy.Spotify(auth=token_info["access_token"]); return self.client
        except Exception as exc:
            print(f"[SpotifyAPI] Client creation error: {exc}"); return None

    def begin_auth(self):
        self._start_auth_flow()

    def _start_auth_flow(self):
        with self.lock:
            if self.auth_in_progress and self.auth_thread and self.auth_thread.is_alive(): return
            self.auth_in_progress = True
            self.auth_state = secrets.token_urlsafe(16)
            auth_url = self._build_auth_url(self.auth_state)
            self.auth_thread = threading.Thread(target=self._run_auth_flow,
                                                args=(auth_url, self.auth_state), daemon=True)
            self.auth_thread.start()

    def _run_auth_flow(self, auth_url, expected_state):
        try:
            code = self._wait_for_auth_code(auth_url, expected_state)
            if not code: return
            token_info = self._exchange_code_for_token(code)
            if token_info is None: return
            self._save_token(token_info)
            self.client = spotipy.Spotify(auth=token_info["access_token"]) if HAS_SPOTIPY else None
            print("[SpotifyAPI] Auth completed successfully")
        except Exception as exc:
            print(f"[SpotifyAPI] Auth flow error: {exc}")
        finally:
            with self.lock:
                self.auth_in_progress = False
                self.auth_thread = None

    def _wait_for_auth_code(self, auth_url, expected_state):
        parsed = urlparse(self.redirect_uri)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 8888
        auth_result = {"code": None, "error": None}
        ready = threading.Event()
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self_inner):
                query = parse_qs(urlparse(self_inner.path).query)
                state = query.get("state", [None])[0]
                code  = query.get("code",  [None])[0]
                error = query.get("error", [None])[0]
                if state != expected_state:  auth_result["error"] = "State mismatch"
                elif error:                  auth_result["error"] = error
                else:                        auth_result["code"]  = code
                body = ("<html><body style='font-family:Segoe UI;background:#121212;color:white;'>"
                        "<h2>Spotify connected.</h2><p>You can close this tab.</p></body></html>")
                self_inner.send_response(200)
                self_inner.send_header("Content-Type", "text/html; charset=utf-8")
                self_inner.send_header("Content-Length", str(len(body.encode())))
                self_inner.end_headers()
                self_inner.wfile.write(body.encode())
                ready.set()
            def log_message(self_inner, *a): return
        server = HTTPServer((host, port), CallbackHandler)
        server.timeout = 120
        try:
            self._open_url(auth_url)
            deadline = time.time() + 120
            while time.time() < deadline and not ready.is_set():
                server.handle_request()
        finally:
            server.server_close()
        if auth_result["error"]: raise RuntimeError(auth_result["error"])
        return auth_result["code"]

    def _open_url(self, url):
        try: os.startfile(url)
        except Exception:
            import webbrowser; webbrowser.open(url, new=2)

    def _build_auth_url(self, state):
        params = {"client_id": self.client_id, "response_type": "code",
                  "redirect_uri": self.redirect_uri, "scope": DEFAULT_SCOPE,
                  "state": state, "show_dialog": "true"}
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def _exchange_code_for_token(self, code):
        try:
            r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                "redirect_uri": self.redirect_uri, "client_id": self.client_id,
                "client_secret": self.client_secret}, timeout=15)
            r.raise_for_status()
            t = r.json(); t["expires_at"] = int(time.time()) + int(t.get("expires_in", 3600)); return t
        except Exception as exc:
            print(f"[SpotifyAPI] Token exchange error: {exc}"); return None

    def _refresh_token(self, refresh_token):
        try:
            r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token",
                "refresh_token": refresh_token, "client_id": self.client_id,
                "client_secret": self.client_secret}, timeout=15)
            r.raise_for_status()
            t = r.json(); t["refresh_token"] = t.get("refresh_token", refresh_token)
            t["expires_at"] = int(time.time()) + int(t.get("expires_in", 3600)); return t
        except Exception as exc:
            print(f"[SpotifyAPI] Refresh error: {exc}"); return None

    def _ensure_valid_token(self, token_info, interactive=False):
        if token_info is None: return None
        if int(token_info.get("expires_at", 0)) > int(time.time()) + 60: return token_info
        refresh = token_info.get("refresh_token")
        if not refresh:
            if interactive: self._start_auth_flow()
            return None
        refreshed = self._refresh_token(refresh)
        if refreshed is not None: self._save_token(refreshed)
        return refreshed

    def _load_token(self):
        if not self.token_path.exists(): return None
        try:
            with self.token_path.open("r", encoding="utf-8") as fh: return json.load(fh)
        except Exception as exc:
            print(f"[SpotifyAPI] Token load error: {exc}"); return None

    def _save_token(self, token_info):
        try:
            with self.token_path.open("w", encoding="utf-8") as fh: json.dump(token_info, fh)
        except Exception as exc:
            print(f"[SpotifyAPI] Token save error: {exc}")

    def forget_account(self):
        self.client = None
        try:
            if self.token_path.exists(): self.token_path.unlink()
        except Exception as exc:
            print(f"[SpotifyAPI] Could not remove token: {exc}")
