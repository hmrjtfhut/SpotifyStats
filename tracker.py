"""Polls the listener once a second, decides when a play "counts", writes to
the database, and pushes updates to the UI.

Play counting rule (similar to how Last.fm scrobbles tracks): a play is
counted once you've actually listened to roughly half the track (capped at
4 minutes), with a 15 second minimum so brief skips don't count.  Counting
happens live, the moment the threshold is crossed, rather than waiting for
the track to end — so the play count updates immediately rather than only
after you skip to the next song.
"""
import threading
import time

from database import SpotifyDatabase
from spotify_listener import SpotifyListener


class SpotifyTracker:
    def __init__(self, ui_update_callback=None):
        self.db = SpotifyDatabase()
        self.listener = SpotifyListener()
        self.ui_callback = ui_update_callback

        self.is_tracking = False
        self._thread = None
        self._tick_interval = 1.0
        self._lock = threading.Lock()

        # Play-counting state
        self._active_key = None
        self._listened_seconds = 0.0
        self._counted = False
        self._last_tick_wall = None
        self._last_position = None
        # Source of the currently tracked song ('local', 'remote', 'window_title', …)
        self._active_source = None
        # Crossfade compensation (seconds). When using window-title tracking the
        # song title flips to the next track at the START of the crossfade, so we
        # would under-count the outgoing song by up to crossfade_sec seconds.
        # Setting this to match Spotify's crossfade setting corrects that.
        # Only applied when source == 'window_title'; exact sources ignore it.
        self.crossfade_sec = 0

        # Keep the last known song visible while paused / briefly unreachable
        self._last_known_song = None
        self._last_known_artist = None

        # Stats only refresh when something changed, or every ~20s as a safety net
        self._stats_dirty = True
        self._refresh_counter = 0
        self._stats_refresh_every = 20

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_tracking(self):
        if self.is_tracking:
            return
        self.is_tracking = True
        try:
            self.db.remove_dj_x_tracks()
        except Exception as exc:
            print(f"[Tracker] Cleanup error: {exc}")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_tracking(self):
        self.is_tracking = False

    def _loop(self):
        while self.is_tracking:
            try:
                self.tick()
            except Exception as exc:
                print(f"[Tracker] Tick error: {exc}")
            time.sleep(self._tick_interval)

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------
    def tick(self):
        with self._lock:
            snapshot = self.listener.get_snapshot()
            now = time.time()
            self._update_play_counter(snapshot, now)
            self._publish(snapshot)

    def _update_play_counter(self, snapshot, now):
        song = snapshot.get("song")
        artist = snapshot.get("artist")
        key = (song, artist) if song and artist else None

        elapsed = 0.0
        if self._last_tick_wall is not None:
            # Cap so resuming after sleep does not credit hours of listening at once
            elapsed = max(0.0, min(now - self._last_tick_wall, 5.0))
        self._last_tick_wall = now

        position = snapshot.get("position_sec")
        is_playing = bool(snapshot.get("is_playing"))

        if key != self._active_key:
            # Song changed or Spotify closed: decide whether the previous song
            # earns a play based on how long we actually listened.
            if self._active_key is not None and not self._counted:
                prev_song, prev_artist = self._active_key
                prev_dur = float(self._active_duration_sec or 0.0)
                # Credit the elapsed time from this tick to the outgoing song
                # before checking the threshold — otherwise the last ~1s is lost.
                outgoing_listened = self._listened_seconds + elapsed
                # Crossfade compensation: window-title tracking sees the title
                # change at the START of Spotify's crossfade, so the outgoing
                # song appears to end early by crossfade_sec seconds.  Add that
                # back so the time credit is accurate.  Exact sources (local
                # WinRT, remote Web API) have precise timing so never need this.
                if self._active_source == "window_title" and self.crossfade_sec > 0:
                    outgoing_listened += float(self.crossfade_sec)
                if outgoing_listened >= self._play_threshold(prev_dur):
                    self._record_play(prev_song, prev_artist, outgoing_listened)
                    self._counted = True

            # Reset for the incoming song
            self._active_key = key
            self._active_source = snapshot.get("source", "none")
            self._active_duration_sec = float(snapshot.get("duration_sec") or 0.0)
            self._listened_seconds = 0.0
            self._counted = False
            self._last_position = position
            return

        if key is None:
            return

        # Keep duration and source up to date in case they arrive late
        self._active_duration_sec = float(snapshot.get("duration_sec") or 0.0) or self._active_duration_sec
        if snapshot.get("source"):
            self._active_source = snapshot.get("source")

        # Large backwards position jump = song repeated; count the previous
        # run if it qualifies, then start fresh.
        if position is not None and self._last_position is not None and position < self._last_position - 5.0:
            if not self._counted:
                prev_dur = float(self._active_duration_sec or 0.0)
                if self._listened_seconds >= self._play_threshold(prev_dur):
                    self._record_play(song, artist, self._listened_seconds)
            self._listened_seconds = 0.0
            self._counted = False

        self._last_position = position

        if is_playing:
            self._listened_seconds += elapsed

    @staticmethod
    def _play_threshold(duration_sec):
        if duration_sec and duration_sec > 0:
            return max(15.0, min(duration_sec * 0.5, 240.0))
        return 30.0

    def _record_play(self, song, artist, listened_seconds):
        try:
            # Store actual time listened, not the full track length.
            # Skip at 1:00 of a 1:30 song -> credits 60s, not 90s.
            duration = int(round(listened_seconds))
            self.db.add_or_update_song(song, artist, duration=duration, play_increment=1)
            for name in self._split_artists(artist):
                self.db.add_or_update_artist(name, duration=duration, play_increment=1)
            self.db.add_listen_history(song, artist, duration)
            self._stats_dirty = True
            print(f"[Tracker] Counted play: {song} by {artist} ({duration}s listened)")
        except Exception as exc:
            print(f"[Tracker] Error recording play: {exc}")

    @staticmethod
    def _split_artists(artist):
        if not artist:
            return []
        parts = [p.strip() for p in artist.replace("&", ",").replace(" feat. ", ",").split(",")]
        return [p for p in parts if p] or [artist]

    # ------------------------------------------------------------------
    # Publishing to the UI
    # ------------------------------------------------------------------
    def _publish(self, snapshot):
        if not self.ui_callback:
            return

        song = snapshot.get("song")
        artist = snapshot.get("artist")
        if song and artist:
            self._last_known_song, self._last_known_artist = song, artist

        display_song = song or self._last_known_song
        display_artist = artist or self._last_known_artist

        plays, duration = 0, 0
        if display_song and display_artist:
            stats = self.db.get_song_stats((display_song, display_artist))
            if stats:
                _, _, plays, duration, _ = stats

        if song and artist:
            playback_state = "playing" if snapshot.get("is_playing") else "paused_closed"
        elif display_song:
            playback_state = "paused_closed" if snapshot.get("connected") else "disconnected"
        else:
            playback_state = "idle"

        self.ui_callback(
            "current_track",
            {
                "song": display_song,
                "artist": display_artist,
                "plays": plays,
                "duration": duration,
                "playback": snapshot,
                "playback_state": playback_state,
            },
        )

        self._refresh_counter += 1
        if self._stats_dirty or self._refresh_counter >= self._stats_refresh_every:
            self._stats_dirty = False
            self._refresh_counter = 0
            self._publish_stats()

    def _publish_stats(self):
        try:
            self.ui_callback("top_songs", self.db.get_all_songs(limit=25))
            self.ui_callback("top_artists", self.db.get_all_artists(limit=25))
            self.ui_callback(
                "totals",
                {
                    "total_songs": self.db.get_total_songs(),
                    "total_minutes": self.db.get_total_minutes(),
                    "total_artists": self.db.get_total_artists(),
                },
            )
        except Exception as exc:
            print(f"[Tracker] Stats refresh error: {exc}")

    # ------------------------------------------------------------------
    # Commands from the UI
    # ------------------------------------------------------------------
    def send_media_command(self, command):
        """Dispatch command off the UI thread; re-tick immediately after so
        the button's effect shows up without waiting for the next 1s poll."""
        def worker():
            try:
                self.listener.send_command(command)
            except Exception as exc:
                print(f"[Tracker] Command error ({command}): {exc}")
            try:
                self.tick()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()