# Spotify Stats Tracker

A Windows app that tracks songs you listen to on Spotify,
shows album art and has playback controls, and keeps a local database of
your listening history. No connected account required to get started, just open Spotify and run the app.

<img width="1013" height="799" alt="Screenshot 2026-07-16 193323" src="https://github.com/user-attachments/assets/a6c981aa-19db-4daf-ad75-f9c9f60d682d" />



## Quick start for exe

Download the exe from the latest release and run it.

## Quick start for code

```
pip install -r requirements.txt
python main.py
```

Python 3.10 or newer is required.



## Files

| File | Purpose |
|---|---|
| `main.py` | App entry point — creates the window and wires everything together |
| `ui.py` | All Tkinter widgets (now-playing card, tables, settings panel) |
| `tracker.py` | Polling loop and play-counting logic |
| `spotify_listener.py` | Reads "what's playing" from Windows / Spotify API |
| `spotify_api.py` | I HIGHLY recommend making your own Spotify web app on Spotify for Developers |
| `database.py` | SQLite helpers (songs, artists, listen history) |
| `tray.py` | System-tray icon (native win32 preferred, pystray fallback) |
| `startup.py` | Windows registry / batch-file run-on-boot helper |
| `build_exe.py` | Builds a standalone `.exe` with PyInstaller |
| `nowplaying_server.py` | Standalone HTTP server for the WinRT media session (separate tool, not used by the app directly) |

---

## How play counting works

A play is counted once you've listened to a whole song or when:

- **half the track's duration**, or
- **4 minutes** (for very long tracks)

…with a hard minimum of **15 seconds** so brief skips never count.

The count updates *immediately* when the threshold is crossed — not after you skip
to the next song — so the number you see is always live.

---

## Spotify account (optional)

The app works fully **without** a Spotify login when the Spotify desktop app is open.
It reads title, artist, album art, position and playback state directly from the
Windows media session API, which needs no credentials and has no rate limits.

Connect a Spotify account only if you also listen on your **phone or another device**.
When you do, the app falls back to the Spotify Web API to read and control remote
playback.

To connect:  open **Settings (⚙)** → *Connect Spotify Account*.
A browser tab will open for the standard Spotify login flow; after authorising you'll
be redirected back to the app automatically.  The token is stored in
`~/.spotistats/spotify_token.json` and refreshes itself silently.

To use your own Spotify Developer app instead of the built-in credentials, edit
`~/.spotistats/spotify_app.json` (created automatically on first run):

```json
{
  "client_id":     "your_client_id",
  "client_secret": "your_client_secret",
  "redirect_uri":  "http://127.0.0.1:8888/callback"
}
```

---

## Building a standalone .exe

```
pip install pyinstaller
python build_exe.py
```

The `.exe` is written to `dist/SpotifyStatsTracker.exe`.  It embeds all
dependencies and runs on any Windows 10/11 machine without a Python install.

---

## What changed from the original version

### Bugs fixed
- **App crashed on startup** — `spotify_listener.py` had an `IndentationError` that
  prevented the module from loading at all.
- **Swapped artist / title** — the old code tried to parse the Spotify window title
  string (`"Song - Artist"`) which guessed wrong frequently.  The new code reads
  `title` and `artist` as separate fields from the Windows media session API.
- **Double / triple counted plays** — three data sources (WinRT, Web API, window title)
  were all running simultaneously and could each trigger a play count for the same
  song.  Now only one source is active at a time.
- **UI froze** — the old tracker spawned a fresh thread on every tick (every 0.5 s);
  those threads piled up.  There is now one persistent polling thread.
- **`build_exe.py` had a hardcoded personal path** (`C:\Users\Travis\…`) so it
  failed on every other machine.

### New features
- **Album art** — pulled from the Windows media session thumbnail (desktop) or the
  Spotify Web API (remote); displayed as a rounded 96×96 image next to the track.
- **Live progress bar** — shows playback position and updates every second.
- **Desktop vs remote badge** — a small pill next to "NOW PLAYING" shows 💻 DESKTOP
  or 📱 REMOTE so you always know which source is active.
- **Sortable, searchable tables** — click any column header to sort; type in the
  search box to filter.
- **Database editor** — Settings → *Edit Database…* lets you fix or delete
  individual entries without touching SQLite directly.
- **Disconnect Spotify account** — Settings → *Disconnect Spotify Account* removes
  the cached token so you can log in with a different account.
- **Repeat-one indicator** — the 🔁 button switches to 🔂 when repeat-one is active.
- **Lyrics removed** — the lyrics overlay existed in the old code but never actually
  fetched anything; it's been removed to simplify the codebase.

---

## Data location

Everything is stored under `~/.spotistats/`:

```
~/.spotistats/
  stats.db            ← SQLite database (songs, artists, history)
  spotify_token.json  ← Spotify OAuth token (only present if you connected)
  spotify_app.json    ← Spotify Developer app credentials override
  app_settings.json   ← Window / UI preferences
  tray_icon.ico       ← Auto-generated tray icon
```
