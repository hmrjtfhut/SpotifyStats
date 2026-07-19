import tkinter as tk
from tkinter import messagebox
import sys
import os
import argparse
import json
import threading
from pathlib import Path
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui import SpotifyStatsUI
from tracker import SpotifyTracker
from startup import AutoStartup
from tray import MinimizeToTray


class SpotifyStatsApp:
    def __init__(self, root, minimized=False):
        self.root = root
        self.minimized = minimized
        self.is_running = True
        self.settings_path = Path.home() / ".spotistats" / "app_settings.json"
        self.app_settings = self._load_settings()

        self.ui = SpotifyStatsUI(root, tracker_callback=self.ui_callback)
        self.tracker = SpotifyTracker(ui_update_callback=self.on_tracker_update)

        # Start tracking after the mainloop starts so root.after is safe
        self.root.after(100, self.tracker.start_tracking)

        self._load_initial_data()

        # Restore saved crossfade setting into the UI slider and tracker
        saved_cf = int(self.app_settings.get("crossfade_sec", 0))
        self.ui.crossfade_var.set(saved_cf)
        self.tracker.crossfade_sec = saved_cf

        self._last_seen_artist = None

        self.tray = MinimizeToTray(root, "Spotify Stats Tracker")
        self.root.bind("<Unmap>", self.tray.on_window_minimize)
        self.ui.hide_button.config(command=self.tray.minimize_to_tray)
        self.ui.erase_button.config(command=self.on_erase_data)

        self._setup_window()
        self.root.after(800, self._maybe_show_spotify_setup)
        self.root.after(4000, self._maybe_backfill_images)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def _load_settings(self):
        defaults = {"never_show_spotify_setup": False, "crossfade_sec": 0}
        try:
            if self.settings_path.exists():
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    defaults.update(data)
        except Exception as e:
            print(f"[App] Error loading settings: {e}")
        return defaults

    def _save_settings(self):
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(self.app_settings), encoding="utf-8")
        except Exception as e:
            print(f"[App] Error saving settings: {e}")

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------
    def _setup_window(self):
        if self.minimized:
            self.root.withdraw()
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth()  // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # UI callback (button presses / settings actions)
    # ------------------------------------------------------------------
    def ui_callback(self, action, data=None):
        if action in {"play_pause", "previous", "next", "toggle_shuffle", "cycle_repeat"}:
            self.tracker.send_media_command(action)
            return

        if action == "reconnect":
            try:
                self.tracker.listener.spotify_api.client = None
                self.tracker.listener.begin_spotify_auth()
            except Exception as e:
                print(f"[App] Reconnect error: {e}")
            return

        if action == "forget_account":
            try:
                self.tracker.listener.forget_spotify_account()
                messagebox.showinfo(
                    "Spotify Disconnected",
                    "Your Spotify account has been disconnected.\n"
                    "You can reconnect any time from Settings.",
                )
            except Exception as e:
                print(f"[App] Forget account error: {e}")
            return

        if action == "set_crossfade":
            seconds = max(0, min(12, int(data or 0)))
            self.tracker.crossfade_sec = seconds
            self.app_settings["crossfade_sec"] = seconds
            self._save_settings()
            return

        if action == "fetch_artist_image":
            # Requested by the artist-detail popup when no cached image URL
            # exists yet. Only produces a result when a Spotify account is
            # connected (the Web API is the only source of artist images).
            artist_name = (data or {}).get("artist")
            callback = (data or {}).get("callback")
            if not artist_name:
                return

            def worker():
                url = None
                try:
                    url = self.ui._images.get_artist_image_url(
                        self.tracker.listener.spotify_api, artist_name
                    )
                    if url:
                        self.tracker.db.set_artist_image(artist_name, url)
                except Exception as e:
                    print(f"[App] Artist image fetch error: {e}")
                if callback:
                    self.root.after(0, lambda: callback(url))

            threading.Thread(target=worker, daemon=True).start()
            return

    # ------------------------------------------------------------------
    # First-run Spotify setup prompt
    # ------------------------------------------------------------------
    def _maybe_show_spotify_setup(self):
        if self.app_settings.get("never_show_spotify_setup"):
            return
        try:
            if self.tracker.listener.spotify_api.has_cached_auth():
                return
        except Exception as e:
            print(f"[App] Auth check error: {e}")
            return
        self._show_spotify_popup()

    def _show_spotify_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Connect Spotify")
        popup.configure(bg="#181818")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()

        w, h = 460, 240
        self.root.update_idletasks()
        x = self.root.winfo_x() + max(20, (self.root.winfo_width()  - w) // 2)
        y = self.root.winfo_y() + max(20, (self.root.winfo_height() - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")

        frame = tk.Frame(popup, bg="#181818")
        frame.pack(fill=tk.BOTH, expand=True, padx=22, pady=22)

        tk.Label(frame, text="Connect a Spotify account?",
                 font=("Segoe UI", 16, "bold"), bg="#181818", fg="#FFFFFF").pack(anchor=tk.W)

        tk.Label(
            frame,
            text=(
                "This is only needed if you sometimes listen on your phone or\n"
                "another device.  While the Spotify desktop app is open, tracking\n"
                "and controls work automatically — no login required."
            ),
            font=("Segoe UI", 10), justify=tk.LEFT, bg="#181818", fg="#B3B3B3",
        ).pack(anchor=tk.W, pady=(12, 20))

        btns = tk.Frame(frame, bg="#181818")
        btns.pack(side=tk.BOTTOM, fill=tk.X)

        def dismiss():    popup.destroy()
        def never():
            self.app_settings["never_show_spotify_setup"] = True
            self._save_settings()
            popup.destroy()
        def connect():
            try:
                self.tracker.listener.begin_spotify_auth()
            except Exception as e:
                print(f"[App] Auth start error: {e}")
            popup.destroy()

        tk.Button(btns, text="Never Show Again", font=("Segoe UI", 9),
                  bg="#2A2A2A", fg="#FFFFFF", relief=tk.FLAT, padx=12, pady=6,
                  command=never).pack(side=tk.LEFT)
        tk.Button(btns, text="Dismiss", font=("Segoe UI", 9),
                  bg="#2A2A2A", fg="#FFFFFF", relief=tk.FLAT, padx=12, pady=6,
                  command=dismiss).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btns, text="Connect Spotify", font=("Segoe UI", 9, "bold"),
                  bg="#1DB954", fg="#FFFFFF", relief=tk.FLAT, padx=14, pady=6,
                  command=connect).pack(side=tk.RIGHT)

        popup.protocol("WM_DELETE_WINDOW", dismiss)

    # ------------------------------------------------------------------
    # Initial data load
    # ------------------------------------------------------------------
    def _load_initial_data(self):
        try:
            self.ui.update_top_songs(self.tracker.db.get_all_songs(limit=25))
            self.ui.update_top_artists(self.tracker.db.get_all_artists(limit=25))
            self.ui.update_totals(
                self.tracker.db.get_total_songs(),
                self.tracker.db.get_total_minutes(),
                self.tracker.db.get_total_artists(),
            )
            # Fire one immediate tick so "Now Playing" shows up right away
            self.tracker.tick()
        except Exception as e:
            print(f"[App] Error loading initial data: {e}")

    # ------------------------------------------------------------------
    # Tracker update handler (runs on the Tk main thread via root.after)
    # ------------------------------------------------------------------
    def on_tracker_update(self, update_type, data):
        self.root.after(0, lambda: self._apply_update(update_type, data))

    def _apply_update(self, update_type, data):
        try:
            if update_type == "current_track":
                self.ui.update_current_track(
                    data["song"], data["artist"], data["plays"], data["duration"],
                    playback=data.get("playback"),
                    playback_state=data.get("playback_state", "idle"),
                )
                artist = data.get("artist")
                if artist and artist != self._last_seen_artist:
                    self._last_seen_artist = artist
                    self._maybe_prefetch_artist_image(artist)
            elif update_type == "top_songs":
                self.ui.update_top_songs(data)
            elif update_type == "top_artists":
                self.ui.update_top_artists(data)
            elif update_type == "totals":
                self.ui.update_totals(
                    data["total_songs"], data["total_minutes"], data.get("total_artists", 0)
                )
            elif update_type == "history_changed":
                self.ui.notify_history_changed()
        except Exception as e:
            print(f"[App] UI update error ({update_type}): {e}")

    def _maybe_prefetch_artist_image(self, artist_name):
        """Fire-and-forget: if this artist has no cached image yet and a
        Spotify account is connected, fetch and store one in the background
        so the Top Artists tab shows real artwork sooner rather than later."""
        try:
            existing = self.tracker.db.get_artist_stats(artist_name)
            if existing and len(existing) > 4 and existing[4]:
                return  # already have an image cached
        except Exception:
            pass

        def worker():
            try:
                url = self.ui._images.get_artist_image_url(
                    self.tracker.listener.spotify_api, artist_name
                )
                if url:
                    self.tracker.db.set_artist_image(artist_name, url)
            except Exception as e:
                print(f"[App] Artist image prefetch error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _maybe_backfill_images(self):
        """One-time background sweep, run shortly after startup, that finds
        every song/artist already in the library missing cover art and
        fetches it — rather than only discovering art reactively as songs
        happen to play. No-ops entirely if no Spotify account is connected,
        since the Web API is the only source of search-by-name lookups."""
        try:
            if not self.tracker.listener.spotify_api.has_cached_auth():
                return
        except Exception:
            return

        def worker():
            try:
                song_result = self.ui._images.backfill_missing_song_art(
                    self.tracker.listener.spotify_api, self.tracker.db, limit=500
                )
                artist_result = self.ui._images.backfill_missing_artist_images(
                    self.tracker.listener.spotify_api, self.tracker.db, limit=300
                )
                print(f"[App] Image backfill done: "
                      f"{song_result.get('found', 0)} song(s), "
                      f"{artist_result.get('found', 0)} artist(s) found")
                if song_result.get("found") or artist_result.get("found"):
                    self.root.after(0, self._refresh_top_lists_after_backfill)
            except Exception as e:
                print(f"[App] Image backfill error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_top_lists_after_backfill(self):
        """Re-render Top Songs / Top Artists so newly-found art appears
        without requiring the user to switch tabs or restart the app."""
        try:
            self.ui.update_top_songs(self.tracker.db.get_all_songs(limit=25))
            self.ui.update_top_artists(self.tracker.db.get_all_artists(limit=25))
        except Exception as e:
            print(f"[App] Post-backfill refresh error: {e}")

    # ------------------------------------------------------------------
    # Erase data
    # ------------------------------------------------------------------
    def on_erase_data(self):
        if not messagebox.askyesno(
            "Confirm Erase",
            "Are you sure you want to erase ALL listening data?\nThis cannot be undone.",
        ):
            return
        try:
            self.tracker.db.clear_all_data()
            self.ui.update_current_track(None, None, 0, 0, playback={}, playback_state="idle")
            self.ui.update_top_songs([])
            self.ui.update_top_artists([])
            self.ui.update_totals(0, 0, 0)
            messagebox.showinfo("Done", "All listening data has been erased.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to erase data:\n{e}")

    # ------------------------------------------------------------------
    # Clean shutdown
    # ------------------------------------------------------------------
    def on_closing(self):
        self.is_running = False
        self.tracker.stop_tracking()
        self.root.destroy()


# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Spotify Stats Tracker")
    parser.add_argument("--minimized",        action="store_true", help="Start minimized to tray")
    parser.add_argument("--enable-startup",   action="store_true", help="Enable run-on-boot")
    parser.add_argument("--disable-startup",  action="store_true", help="Disable run-on-boot")
    args = parser.parse_args()

    if args.enable_startup:
        print("Startup enabled!" if AutoStartup.enable_startup() else "Failed to enable startup")
        return
    if args.disable_startup:
        print("Startup disabled!" if AutoStartup.disable_startup() else "Failed to disable startup")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    root = tk.Tk()
    SpotifyStatsApp(root, minimized=args.minimized)
    root.mainloop()


if __name__ == "__main__":
    main()
