import sqlite3
import os
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
    
    def init_db(self):
        """Initialize database tables"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        # Songs table
        # Migrate existing schema if it used a UNIQUE(title) constraint
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='songs'")
        row = cursor.fetchone()
        need_migrate = False
        if row and row[0]:
            existing_sql = row[0].lower()
            # detect old schema that declared title as UNIQUE (single-column)
            if 'title text unique' in existing_sql or 'unique(title)' in existing_sql:
                need_migrate = True

        if need_migrate:
            # Rename old table and create a new one with UNIQUE(title, artist)
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
                        UNIQUE(title, artist)
                    )
                ''')
                # Copy rows across; previous table had UNIQUE(title) so no duplicate titles exist.
                cursor.execute('INSERT INTO songs (title, artist, play_count, total_duration, last_played, created_at) SELECT title, artist, play_count, total_duration, last_played, created_at FROM songs_old')
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
                    UNIQUE(title, artist)
                )
            ''')
        
        # Artists table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                play_count INTEGER DEFAULT 1,
                total_duration INTEGER DEFAULT 0,
                last_played TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Listen history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS listen_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                duration INTEGER,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def add_or_update_song(self, title, artist, duration=0, play_increment=1):
        """Add or update song statistics"""
        # Normalize inputs
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        # Support incrementing play_count by more than 1 when a long
        # continuous playback should be counted as multiple plays.
        # Use SQLite UPSERT on the (title, artist) unique key so we never
        # accidentally update a different artist with the same title.
        try:
            cursor.execute('''
                INSERT INTO songs (title, artist, play_count, total_duration, last_played)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(title, artist) DO UPDATE SET
                    play_count = play_count + excluded.play_count,
                    total_duration = total_duration + excluded.total_duration,
                    last_played = CURRENT_TIMESTAMP
            ''', (title, artist, int(play_increment), duration))
        except sqlite3.IntegrityError:
            # As a fallback for older SQLite versions, try a SELECT/UPDATE path
            cursor.execute('SELECT id FROM songs WHERE title = ? AND artist = ?', (title, artist))
            if cursor.fetchone():
                cursor.execute('''
                    UPDATE songs 
                    SET play_count = play_count + ?,
                        total_duration = total_duration + ?,
                        last_played = CURRENT_TIMESTAMP
                    WHERE title = ? AND artist = ?
                ''', (int(play_increment), duration, title, artist))
            else:
                cursor.execute('''
                    INSERT INTO songs (title, artist, play_count, total_duration, last_played)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (title, artist, int(play_increment), duration))
        
        conn.commit()
        conn.close()
    
    def add_or_update_artist(self, name, duration=0, play_increment=1):
        """Add or update artist statistics"""
        # Normalize inputs
        name = (name or "").strip()
        if not name:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO artists (name, play_count, total_duration, last_played)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (name, int(play_increment), duration))
        except sqlite3.IntegrityError:
            cursor.execute('''
                UPDATE artists 
                SET play_count = play_count + ?,
                    total_duration = total_duration + ?,
                    last_played = CURRENT_TIMESTAMP
                WHERE name = ?
            ''', (int(play_increment), duration, name))
        
        conn.commit()
        conn.close()
    
    def add_listen_history(self, title, artist, duration=0):
        """Add to listen history"""
        # Normalize inputs
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title or not artist:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO listen_history (title, artist, duration)
            VALUES (?, ?, ?)
        ''', (title, artist, duration))
        conn.commit()
        conn.close()

    def add_listen_history_multiple(self, title, artist, durations):
        """Insert multiple listen history rows.

        `durations` should be an iterable of integer durations (seconds) for
        each counted play. This preserves historical granularity when a
        long continuous playback should be counted as multiple plays.
        """
        if not durations:
            return
        # Normalize inputs
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title or not artist:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        entries = [(title, artist, int(d)) for d in durations]
        cursor.executemany('''
            INSERT INTO listen_history (title, artist, duration)
            VALUES (?, ?, ?)
        ''', entries)
        conn.commit()
        conn.close()
    
    def get_song_stats(self, title):
        """Get statistics for a specific song"""
        conn = self.get_connection()
        cursor = conn.cursor()
        # Support optional artist disambiguation by accepting a tuple
        # like (title, artist) or a single title string. If only title is
        # provided, return the most recently played matching title.
        if isinstance(title, (list, tuple)) and len(title) >= 2:
            t, a = title[0], title[1]
            cursor.execute('''
                SELECT title, artist, play_count, total_duration, last_played
                FROM songs WHERE title = ? AND artist = ?
            ''', (t, a))
        else:
            cursor.execute('''
                SELECT title, artist, play_count, total_duration, last_played
                FROM songs WHERE title = ?
                ORDER BY last_played DESC
                LIMIT 1
            ''', (title,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_artist_stats(self, name):
        """Get statistics for a specific artist"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, play_count, total_duration, last_played
            FROM artists WHERE name = ?
        ''', (name,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_all_songs(self, limit=50):
        """Get top songs by play count"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT title, artist, play_count, total_duration, last_played
            FROM songs
            ORDER BY play_count DESC, last_played DESC
            LIMIT ?
        ''', (limit,))
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_all_artists(self, limit=50):
        """Get top artists by play count"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, play_count, total_duration, last_played
            FROM artists
            ORDER BY play_count DESC, last_played DESC
            LIMIT ?
        ''', (limit,))
        results = cursor.fetchall()
        conn.close()
        return results
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def get_total_songs(self):
        """Get total number of song plays (include repeated plays).

        This counts every entry in the listen_history table so repeats
        (multiple plays of the same song) are included.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM listen_history')
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def get_total_artists(self):
        """Get total number of unique artists (no repeats)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM artists')
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def get_total_minutes(self):
        """Get total minutes listened"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COALESCE(SUM(total_duration), 0) FROM songs')
        total_seconds = cursor.fetchone()[0]
        conn.close()
        return total_seconds // 60  # Convert to minutes
    
    def remove_dj_x_tracks(self):
        """Remove DJ X 'Up next' tracks that should not be counted"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Delete DJ X Up next from songs, artists, and listen_history
            cursor.execute("DELETE FROM songs WHERE title = 'Up next' AND artist = 'DJ X'")
            cursor.execute("DELETE FROM listen_history WHERE title = 'Up next' AND artist = 'DJ X'")
            
            # Check if DJ X artist record has other songs; if not, delete it
            cursor.execute("SELECT COUNT(*) FROM songs WHERE artist = 'DJ X'")
            count = cursor.fetchone()[0]
            if count == 0:
                cursor.execute("DELETE FROM artists WHERE name = 'DJ X'")
            
            conn.commit()
            print("[Database] Removed DJ X 'Up Next' tracks from history")
        except Exception as e:
            print(f"[Database] Error removing DJ X tracks: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def clear_all_data(self):
        """Clear all data from database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Delete all data from tables
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
