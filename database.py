import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


class SpotifyDatabase:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(Path.home(), '.spotistats', 'stats.db')

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn = None
        self.init_db()

    def get_connection(self):
        """Get a thread-safe database connection"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def init_db(self):
        """Initialize database tables"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        # Songs table -- migrate old UNIQUE(title) schema if present
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='songs'")
        row = cursor.fetchone()
        need_migrate = False
        if row and row[0]:
            existing_sql = row[0].lower()
            if 'title text unique' in existing_sql or 'unique(title)' in existing_sql:
                need_migrate = True

        if need_migrate:
            try:
                cursor.execute('ALTER TABLE songs RENAME TO songs_old')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS songs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        artist TEXT NOT NULL,
                        play_count INTEGER DEFAULT 1,
                        total_duration INTEGER DEFAULT 0,
                        last_played TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        album_art_url TEXT,
                        UNIQUE(title, artist)
                    )
                ''')
                cursor.execute(
                    'INSERT INTO songs (title, artist, play_count, total_duration, last_played, created_at) '
                    'SELECT title, artist, play_count, total_duration, last_played, created_at FROM songs_old'
                )
                cursor.execute('DROP TABLE songs_old')
                self.conn.commit()
                print('[Database] Migrated songs table to UNIQUE(title, artist)')
            except Exception as e:
                print(f"[Database] Migration failed: {e}")
                self.conn.rollback()
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    play_count INTEGER DEFAULT 1,
                    total_duration INTEGER DEFAULT 0,
                    last_played TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    album_art_url TEXT,
                    UNIQUE(title, artist)
                )
            ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                play_count INTEGER DEFAULT 1,
                total_duration INTEGER DEFAULT 0,
                last_played TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                image_url TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS listen_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                duration INTEGER,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'live'
            )
        ''')

        # Add columns to pre-existing databases that predate this version.
        self._ensure_column(cursor, 'songs', 'album_art_url', 'TEXT')
        self._ensure_column(cursor, 'artists', 'image_url', 'TEXT')
        self._ensure_column(cursor, 'listen_history', 'source', "TEXT DEFAULT 'live'")

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_played_at ON listen_history(played_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_artist ON listen_history(artist)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_title_artist ON listen_history(title, artist)')

        self.conn.commit()

    @staticmethod
    def _ensure_column(cursor, table, column, col_type):
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def add_or_update_song(self, title, artist, duration=0, play_increment=1, album_art_url=None):
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO songs (title, artist, play_count, total_duration, last_played, album_art_url)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(title, artist) DO UPDATE SET
                    play_count = play_count + excluded.play_count,
                    total_duration = total_duration + excluded.total_duration,
                    last_played = CURRENT_TIMESTAMP,
                    album_art_url = COALESCE(excluded.album_art_url, songs.album_art_url)
            ''', (title, artist, int(play_increment), duration, album_art_url))
        except sqlite3.IntegrityError:
            cursor.execute('SELECT id FROM songs WHERE title = ? AND artist = ?', (title, artist))
            if cursor.fetchone():
                cursor.execute('''
                    UPDATE songs
                    SET play_count = play_count + ?,
                        total_duration = total_duration + ?,
                        last_played = CURRENT_TIMESTAMP,
                        album_art_url = COALESCE(?, album_art_url)
                    WHERE title = ? AND artist = ?
                ''', (int(play_increment), duration, album_art_url, title, artist))
            else:
                cursor.execute('''
                    INSERT INTO songs (title, artist, play_count, total_duration, last_played, album_art_url)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ''', (title, artist, int(play_increment), duration, album_art_url))

        conn.commit()
        conn.close()

    def add_or_update_artist(self, name, duration=0, play_increment=1, image_url=None):
        name = (name or "").strip()
        if not name:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO artists (name, play_count, total_duration, last_played, image_url)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
            ''', (name, int(play_increment), duration, image_url))
        except sqlite3.IntegrityError:
            cursor.execute('''
                UPDATE artists
                SET play_count = play_count + ?,
                    total_duration = total_duration + ?,
                    last_played = CURRENT_TIMESTAMP,
                    image_url = COALESCE(?, image_url)
                WHERE name = ?
            ''', (int(play_increment), duration, image_url, name))

        conn.commit()
        conn.close()

    def set_song_art(self, title, artist, album_art_url):
        if not album_art_url:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE songs SET album_art_url = ? WHERE title = ? AND artist = ? AND album_art_url IS NULL',
            (album_art_url, title, artist),
        )
        conn.commit()
        conn.close()

    def set_artist_image(self, name, image_url):
        if not image_url:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE artists SET image_url = ? WHERE name = ? AND image_url IS NULL',
            (image_url, name),
        )
        conn.commit()
        conn.close()

    def add_listen_history(self, title, artist, duration=0, source='live', played_at=None):
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title or not artist:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        if played_at:
            cursor.execute(
                'INSERT INTO listen_history (title, artist, duration, played_at, source) VALUES (?, ?, ?, ?, ?)',
                (title, artist, duration, played_at, source),
            )
        else:
            cursor.execute(
                'INSERT INTO listen_history (title, artist, duration, source) VALUES (?, ?, ?, ?)',
                (title, artist, duration, source),
            )
        conn.commit()
        conn.close()

    def add_listen_history_multiple(self, title, artist, durations):
        if not durations:
            return
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title or not artist:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        entries = [(title, artist, int(d)) for d in durations]
        cursor.executemany(
            'INSERT INTO listen_history (title, artist, duration) VALUES (?, ?, ?)', entries
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Reads -- top lists
    # ------------------------------------------------------------------
    def get_song_stats(self, title):
        conn = self.get_connection()
        cursor = conn.cursor()
        if isinstance(title, (list, tuple)) and len(title) >= 2:
            t, a = title[0], title[1]
            cursor.execute(
                'SELECT title, artist, play_count, total_duration, last_played, album_art_url '
                'FROM songs WHERE title = ? AND artist = ?',
                (t, a),
            )
        else:
            cursor.execute(
                'SELECT title, artist, play_count, total_duration, last_played, album_art_url '
                'FROM songs WHERE title = ? ORDER BY last_played DESC LIMIT 1',
                (title,),
            )
        result = cursor.fetchone()
        conn.close()
        return result

    def get_artist_stats(self, name):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT name, play_count, total_duration, last_played, image_url FROM artists WHERE name = ?',
            (name,),
        )
        result = cursor.fetchone()
        conn.close()
        return result

    def get_all_songs(self, limit=50, search=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        if search:
            like = f"%{search}%"
            cursor.execute('''
                SELECT title, artist, play_count, total_duration, last_played, album_art_url
                FROM songs WHERE title LIKE ? OR artist LIKE ?
                ORDER BY play_count DESC, last_played DESC LIMIT ?
            ''', (like, like, limit))
        else:
            cursor.execute('''
                SELECT title, artist, play_count, total_duration, last_played, album_art_url
                FROM songs ORDER BY play_count DESC, last_played DESC LIMIT ?
            ''', (limit,))
        results = cursor.fetchall()
        conn.close()
        return results

    def get_all_artists(self, limit=50, search=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        if search:
            like = f"%{search}%"
            cursor.execute('''
                SELECT name, play_count, total_duration, last_played, image_url
                FROM artists WHERE name LIKE ?
                ORDER BY play_count DESC, last_played DESC LIMIT ?
            ''', (like, limit))
        else:
            cursor.execute('''
                SELECT name, play_count, total_duration, last_played, image_url
                FROM artists ORDER BY play_count DESC, last_played DESC LIMIT ?
            ''', (limit,))
        results = cursor.fetchall()
        conn.close()
        return results

    # ------------------------------------------------------------------
    # Reads -- listening history (paginated + searchable)
    # ------------------------------------------------------------------
    def get_listen_history(self, limit=100, offset=0, search=None, artist=None):
        """Return raw play-by-play history, most recent first."""
        conn = self.get_connection()
        cursor = conn.cursor()
        clauses = []
        params = []
        if search:
            clauses.append("(title LIKE ? OR artist LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if artist:
            clauses.append("artist = ?")
            params.append(artist)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params += [limit, offset]
        cursor.execute(f'''
            SELECT id, title, artist, duration, played_at, source
            FROM listen_history
            {where}
            ORDER BY played_at DESC, id DESC
            LIMIT ? OFFSET ?
        ''', params)
        results = cursor.fetchall()
        conn.close()
        return results

    def get_listen_history_count(self, search=None, artist=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        clauses = []
        params = []
        if search:
            clauses.append("(title LIKE ? OR artist LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if artist:
            clauses.append("artist = ?")
            params.append(artist)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor.execute(f"SELECT COUNT(*) FROM listen_history {where}", params)
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def get_recent_plays(self, limit=25):
        """Most recently played tracks -- used for a live 'recently played' feed."""
        return self.get_listen_history(limit=limit, offset=0)

    # ------------------------------------------------------------------
    # Reads -- per-artist detail (drill-down view)
    # ------------------------------------------------------------------
    def get_songs_by_artist(self, artist_name, limit=200):
        """All songs credited to a given artist, sorted by play count."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT title, artist, play_count, total_duration, last_played, album_art_url
            FROM songs
            WHERE artist = ? OR artist LIKE ? OR artist LIKE ? OR artist LIKE ?
            ORDER BY play_count DESC, total_duration DESC
            LIMIT ?
        ''', (artist_name, f"{artist_name}, %", f"%, {artist_name}", f"%, {artist_name}, %", limit))
        results = cursor.fetchall()
        conn.close()
        return results

    def get_artist_first_last_played(self, artist_name):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MIN(played_at), MAX(played_at) FROM listen_history WHERE artist = ?
        ''', (artist_name,))
        row = cursor.fetchone()
        conn.close()
        return (row[0], row[1]) if row else (None, None)

    def get_artist_daily_totals(self, artist_name, days=30):
        """Minutes listened per day for the last N days -- used for a small
        listening-activity chart on the artist detail page."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date(played_at) as day, COALESCE(SUM(duration), 0) as total
            FROM listen_history
            WHERE artist = ? AND played_at >= datetime('now', ?)
            GROUP BY day
            ORDER BY day ASC
        ''', (artist_name, f'-{int(days)} days'))
        results = cursor.fetchall()
        conn.close()
        return results

    # ------------------------------------------------------------------
    # Reads -- year-over-year / month-over-month breakdowns (stat card
    # click-through charts)
    # ------------------------------------------------------------------
    def get_yearly_stats(self):
        """Per-year totals: (year, play_count, total_seconds, unique_artists),
        oldest first. Rows with no parseable played_at are excluded."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT strftime('%Y', played_at) as yr,
                   COUNT(*) as plays,
                   COALESCE(SUM(duration), 0) as total_seconds,
                   COUNT(DISTINCT artist) as artists
            FROM listen_history
            WHERE played_at IS NOT NULL
            GROUP BY yr
            ORDER BY yr ASC
        ''')
        results = [r for r in cursor.fetchall() if r[0]]
        conn.close()
        return results

    def get_monthly_stats(self, months_back=24):
        """Per-month totals for the last N months: (year_month 'YYYY-MM',
        play_count, total_seconds, unique_artists), oldest first."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT strftime('%Y-%m', played_at) as ym,
                   COUNT(*) as plays,
                   COALESCE(SUM(duration), 0) as total_seconds,
                   COUNT(DISTINCT artist) as artists
            FROM listen_history
            WHERE played_at >= datetime('now', ?) AND played_at IS NOT NULL
            GROUP BY ym
            ORDER BY ym ASC
        ''', (f'-{int(months_back)} months',))
        results = [r for r in cursor.fetchall() if r[0]]
        conn.close()
        return results

    def get_monthly_stats_for_year(self, year):
        """Per-month totals restricted to a single calendar year."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT strftime('%Y-%m', played_at) as ym,
                   COUNT(*) as plays,
                   COALESCE(SUM(duration), 0) as total_seconds,
                   COUNT(DISTINCT artist) as artists
            FROM listen_history
            WHERE strftime('%Y', played_at) = ? AND played_at IS NOT NULL
            GROUP BY ym
            ORDER BY ym ASC
        ''', (str(year),))
        results = cursor.fetchall()
        conn.close()
        return results

    # ------------------------------------------------------------------
    # Reads -- per-song play history (for the song detail popup)
    # ------------------------------------------------------------------
    def get_song_history(self, title, artist, limit=50):
        """Individual play timestamps for one specific song, most recent first."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT played_at, duration, source
            FROM listen_history
            WHERE title = ? AND artist = ?
            ORDER BY played_at DESC, id DESC
            LIMIT ?
        ''', (title, artist, limit))
        results = cursor.fetchall()
        conn.close()
        return results

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    def close(self):
        if self.conn:
            self.conn.close()

    def get_total_songs(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM listen_history')
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def get_total_artists(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM artists')
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def get_total_minutes(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COALESCE(SUM(total_duration), 0) FROM songs')
        total_seconds = cursor.fetchone()[0]
        conn.close()
        return total_seconds // 60

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def remove_dj_x_tracks(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM songs WHERE title = 'Up next' AND artist = 'DJ X'")
            cursor.execute("DELETE FROM listen_history WHERE title = 'Up next' AND artist = 'DJ X'")
            cursor.execute("SELECT COUNT(*) FROM songs WHERE artist = 'DJ X'")
            if cursor.fetchone()[0] == 0:
                cursor.execute("DELETE FROM artists WHERE name = 'DJ X'")
            conn.commit()
            print("[Database] Removed DJ X 'Up Next' tracks from history")
        except Exception as e:
            print(f"[Database] Error removing DJ X tracks: {e}")
            conn.rollback()
        finally:
            conn.close()

    def clear_all_data(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM songs')
            cursor.execute('DELETE FROM artists')
            cursor.execute('DELETE FROM listen_history')
            conn.commit()
            print("[Database] All data cleared!")
        except Exception as e:
            print(f"[Database] Error clearing data: {e}")
            conn.rollback()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Spotify Data Export import
    # ------------------------------------------------------------------
    def import_spotify_export(self, file_paths, progress_callback=None):
        """Import one or more Spotify "Extended Streaming History" or
        "Account Data" JSON export files.

        Recognised record shapes (Spotify has used both over the years):
          Extended:  {"ts": "...", "ms_played": 1234,
                      "master_metadata_track_name": "...",
                      "master_metadata_album_artist_name": "..."}
          Legacy:    {"endTime": "2024-01-01 12:00", "artistName": "...",
                      "trackName": "...", "msPlayed": 1234}

        Returns a dict: {"imported": N, "skipped": N, "files": N, "errors": [...]}
        """
        imported = 0
        skipped = 0
        errors = []
        files_done = 0

        conn = self.get_connection()
        cursor = conn.cursor()

        song_totals = {}    # (title, artist) -> [play_count, total_duration, last_played]
        artist_totals = {}  # artist -> [play_count, total_duration, last_played]
        history_rows = []   # (title, artist, duration, played_at, source)

        for path in file_paths:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                continue

            if not isinstance(data, list):
                errors.append(f"{os.path.basename(path)}: not a JSON array, skipped")
                continue

            for entry in data:
                try:
                    title = entry.get("master_metadata_track_name") or entry.get("trackName")
                    artist = (
                        entry.get("master_metadata_album_artist_name")
                        or entry.get("artistName")
                    )
                    ms_played = entry.get("ms_played")
                    if ms_played is None:
                        ms_played = entry.get("msPlayed", 0)
                    played_at = entry.get("ts") or entry.get("endTime")

                    if not title or not artist:
                        skipped += 1
                        continue

                    title = title.strip()
                    artist = artist.strip()
                    duration_sec = int(round((ms_played or 0) / 1000))

                    # Spotify counts anything played briefly; we use a 15s
                    # floor to match the app's own live-tracking threshold so
                    # imported history and live history stay consistent.
                    if duration_sec < 15:
                        skipped += 1
                        continue

                    normalized_ts = self._normalize_timestamp(played_at)

                    key = (title, artist)
                    if key not in song_totals:
                        song_totals[key] = [0, 0, normalized_ts]
                    song_totals[key][0] += 1
                    song_totals[key][1] += duration_sec
                    if normalized_ts and (not song_totals[key][2] or normalized_ts > song_totals[key][2]):
                        song_totals[key][2] = normalized_ts

                    for name in self._split_artist_names(artist):
                        if name not in artist_totals:
                            artist_totals[name] = [0, 0, normalized_ts]
                        artist_totals[name][0] += 1
                        artist_totals[name][1] += duration_sec
                        if normalized_ts and (not artist_totals[name][2] or normalized_ts > artist_totals[name][2]):
                            artist_totals[name][2] = normalized_ts

                    history_rows.append((title, artist, duration_sec, normalized_ts, "import"))
                    imported += 1
                except Exception:
                    skipped += 1
                    continue

            files_done += 1
            if progress_callback:
                try:
                    progress_callback(files_done, len(file_paths), imported)
                except Exception:
                    pass

        # Bulk-apply to the database
        try:
            for (title, artist), (count, duration, last_played) in song_totals.items():
                cursor.execute('''
                    INSERT INTO songs (title, artist, play_count, total_duration, last_played)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(title, artist) DO UPDATE SET
                        play_count = play_count + excluded.play_count,
                        total_duration = total_duration + excluded.total_duration,
                        last_played = MAX(last_played, excluded.last_played)
                ''', (title, artist, count, duration, last_played))

            for name, (count, duration, last_played) in artist_totals.items():
                cursor.execute('''
                    INSERT INTO artists (name, play_count, total_duration, last_played)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        play_count = play_count + excluded.play_count,
                        total_duration = total_duration + excluded.total_duration,
                        last_played = MAX(last_played, excluded.last_played)
                ''', (name, count, duration, last_played))

            cursor.executemany('''
                INSERT INTO listen_history (title, artist, duration, played_at, source)
                VALUES (?, ?, ?, ?, ?)
            ''', history_rows)

            conn.commit()
        except Exception as e:
            conn.rollback()
            errors.append(f"Database write error: {e}")
        finally:
            conn.close()

        return {
            "imported": imported,
            "skipped": skipped,
            "files": files_done,
            "songs": len(song_totals),
            "artists": len(artist_totals),
            "errors": errors,
        }

    @staticmethod
    def _split_artist_names(artist):
        if not artist:
            return []
        parts = [p.strip() for p in artist.replace("&", ",").replace(" feat. ", ",").split(",")]
        return [p for p in parts if p] or [artist]

    @staticmethod
    def _normalize_timestamp(raw_ts):
        """Spotify exports use ISO8601 ('2024-01-01T12:00:00Z') for the new
        format and 'YYYY-MM-DD HH:MM' for the legacy format. Normalize both
        to the 'YYYY-MM-DD HH:MM:SS' format SQLite expects for comparisons."""
        if not raw_ts:
            return None
        try:
            cleaned = raw_ts.replace("Z", "").replace("T", " ")
            if len(cleaned) == 16:  # "YYYY-MM-DD HH:MM"
                cleaned += ":00"
            dt = datetime.strptime(cleaned[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
