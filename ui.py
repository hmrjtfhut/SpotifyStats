import io
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from database import SpotifyDatabase
from image_cache import ImageCache


class SpotifyStatsUI:
    # -- Palette --------------------------------------------------------
    DARK_BG        = "#0E0E10"
    CARD_BG        = "#181818"
    CARD_BG_2      = "#202022"
    ROW_ALT        = "#1B1B1D"
    TEXT_PRIMARY   = "#FFFFFF"
    TEXT_SECONDARY = "#B3B3B3"
    TEXT_MUTED     = "#6E6E6E"
    ACCENT_GREEN   = "#1DB954"
    ACCENT_GREEN_H = "#1ed760"
    ACCENT_BLUE    = "#4A90E2"
    ACCENT_RED     = "#E0455A"
    FONT           = "Segoe UI"

    ICON_SIZE  = 28
    HIST_PAGE_SIZE = 50

    def __init__(self, root, tracker_callback=None):
        self.root = root
        self.tracker_callback = tracker_callback
        self.settings_menu = None
        self.db_window     = None
        self._db_editor_db = None

        # Independent read-only DB handle + image cache for the new tabs
        # (History, Import, artist drill-down). This mirrors the pattern the
        # database editor already used, so main.py needs almost no changes.
        self._db    = SpotifyDatabase()
        self._images = ImageCache()

        # Visibility toggles
        self.show_controls    = tk.BooleanVar(value=True)
        self.show_totals       = tk.BooleanVar(value=True)
        self.always_on_top     = tk.BooleanVar(value=False)

        # Crossfade compensation (0-12s); only active when source is window_title
        self.crossfade_var     = tk.IntVar(value=0)
        self._last_source       = None
        self._crossfade_slider  = None

        # Icon caches (kept alive so Tk doesn't GC them)
        self._song_icon_cache   = {}   # (title, artist) -> PhotoImage
        self._artist_icon_cache = {}   # artist name      -> PhotoImage
        self._icon_fetch_inflight = set()

        # Art cache for the Now Playing card
        self._art_photo     = None
        self._art_cache_key = None

        # Table state
        self._songs_data      = []
        self._artists_data     = []
        self._songs_sort       = ("plays", True)
        self._artists_sort     = ("plays", True)
        self._songs_filter      = tk.StringVar()
        self._artists_filter    = tk.StringVar()
        self._songs_filter.trace_add("write",   lambda *_: self._render_songs_table())
        self._artists_filter.trace_add("write", lambda *_: self._render_artists_table())

        # History tab state
        self._history_search = tk.StringVar()
        self._history_page   = 0
        self._history_total  = 0
        self._history_search.trace_add("write", lambda *_: self._load_history_page(reset=True))

        # Import tab state
        self._import_running = False

        self.root.title("Spotify Stats Tracker")
        self.root.geometry("1080x840")
        self.root.minsize(820, 600)
        self.root.configure(bg=self.DARK_BG)
        self._set_window_icon()
        self._setup_styles()
        self._build_placeholder_icons()
        self._build_ui()

    # -- Icon / window icon setup ----------------------------------------
    def _set_window_icon(self):
        try:
            self._icon_photo = ImageTk.PhotoImage(self._placeholder_art(48))
            self.root.iconphoto(False, self._icon_photo)
        except Exception:
            pass

    def _build_placeholder_icons(self):
        self._placeholder_song_icon   = ImageTk.PhotoImage(self._make_note_icon(self.ICON_SIZE))
        self._placeholder_artist_icon = ImageTk.PhotoImage(self._make_avatar_icon(self.ICON_SIZE))

    @classmethod
    def _make_note_icon(cls, size):
        img = Image.new("RGBA", (size, size), (29, 185, 84, 255))
        d = ImageDraw.Draw(img)
        w = "white"
        d.rectangle([size//2-1, size//4, size//2+1, size//2+size//4], fill=w)
        cy = size//2 + size//6
        d.ellipse([size//2-6, cy-4, size//2, cy+2], fill=w)
        d.ellipse([size//2+2, cy,   size//2+8, cy+6], fill=w)
        return cls._rounded(img, radius=size//5)

    @classmethod
    def _make_avatar_icon(cls, size):
        img = Image.new("RGBA", (size, size), (74, 144, 226, 255))
        d = ImageDraw.Draw(img)
        w = "white"
        d.ellipse([size*0.32, size*0.18, size*0.68, size*0.54], fill=w)          # head
        d.pieslice([size*0.15, size*0.5, size*0.85, size*1.15], 200, 340, fill=w)  # shoulders
        return cls._rounded(img, radius=size//5)

    # -- ttk styles -------------------------------------------------------
    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.configure("TFrame", background=self.DARK_BG)
        s.configure("TLabel", background=self.DARK_BG, foreground=self.TEXT_PRIMARY)

        s.configure("Spotify.Horizontal.TProgressbar",
                    troughcolor=self.CARD_BG_2, background=self.ACCENT_GREEN,
                    bordercolor=self.CARD_BG, lightcolor=self.ACCENT_GREEN,
                    darkcolor=self.ACCENT_GREEN, thickness=5)

        s.configure("Dark.Treeview",
                    background=self.CARD_BG, fieldbackground=self.CARD_BG,
                    foreground=self.TEXT_PRIMARY, borderwidth=0, rowheight=32,
                    font=(self.FONT, 9))
        s.map("Dark.Treeview",
              background=[("selected", "#2A2A2A")],
              foreground=[("selected", self.TEXT_PRIMARY)])
        s.configure("Dark.Treeview.Heading",
                    background=self.CARD_BG_2, foreground=self.TEXT_SECONDARY,
                    borderwidth=0, font=(self.FONT, 9, "bold"), relief=tk.FLAT)
        s.map("Dark.Treeview.Heading", background=[("active", self.CARD_BG_2)])

        s.configure("Dark.TNotebook", background=self.DARK_BG, borderwidth=0)
        s.configure("Dark.TNotebook.Tab", background=self.CARD_BG_2,
                    foreground=self.TEXT_SECONDARY, padding=(14, 8),
                    font=(self.FONT, 9, "bold"))
        s.map("Dark.TNotebook.Tab",
              background=[("selected", self.CARD_BG)],
              foreground=[("selected", self.ACCENT_GREEN)])

    # -- Top-level layout ---------------------------------------------------
    def _build_ui(self):
        self._build_header(self.root)

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        self._build_now_playing(self.main_frame)
        self._build_totals(self.main_frame)

        self.notebook = ttk.Notebook(self.main_frame, style="Dark.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        self.tab_songs   = self._build_songs_tab(self.notebook)
        self.tab_artists = self._build_artists_tab(self.notebook)
        self.tab_history = self._build_history_tab(self.notebook)
        self.tab_import  = self._build_import_tab(self.notebook)

        self.notebook.add(self.tab_songs,   text="Top Songs")
        self.notebook.add(self.tab_artists, text="Top Artists")
        self.notebook.add(self.tab_history, text="History")
        self.notebook.add(self.tab_import,  text="Import Data")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._apply_visibility()
        self._load_history_page(reset=True)

    def _on_tab_changed(self, _event=None):
        try:
            current = self.notebook.tab(self.notebook.select(), "text")
        except Exception:
            return
        if current == "History":
            self._load_history_page(reset=False)

    # -- Header ---------------------------------------------------------
    def _build_header(self, parent):
        header = tk.Frame(parent, bg=self.CARD_BG, height=68)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=18, pady=12)

        tk.Label(inner, text="\u266a  Spotify Stats",
                 font=(self.FONT, 20, "bold"), bg=self.CARD_BG, fg=self.ACCENT_GREEN
                 ).pack(side=tk.LEFT)

        btns = tk.Frame(inner, bg=self.CARD_BG)
        btns.pack(side=tk.RIGHT)

        self.settings_button = tk.Button(
            btns, text="\u2699", font=("Segoe UI Symbol", 13),
            bg=self.DARK_BG, fg=self.TEXT_PRIMARY, relief=tk.FLAT, width=3,
            activebackground=self.CARD_BG_2, command=self._toggle_settings)
        self.settings_button.pack(side=tk.LEFT, padx=(0, 8))

        self.erase_button = tk.Button(
            btns, text="Erase Data", font=(self.FONT, 9),
            bg=self.ACCENT_RED, fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=12, pady=5,
            activebackground="#c93b4e")
        self.erase_button.pack(side=tk.LEFT, padx=(0, 8))

        self.hide_button = tk.Button(
            btns, text="Hide to Tray", font=(self.FONT, 9),
            bg=self.ACCENT_GREEN, fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=15, pady=5,
            activebackground=self.ACCENT_GREEN_H)
        self.hide_button.pack(side=tk.LEFT)

    # -- Now-playing card -------------------------------------------------
    def _build_now_playing(self, parent):
        self.current_frame = tk.Frame(parent, bg=self.CARD_BG)
        self.current_frame.pack(fill=tk.X)

        inner = tk.Frame(self.current_frame, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=20, pady=20)

        self.art_label = tk.Label(inner, bg=self.CARD_BG, bd=0)
        self.art_label.pack(side=tk.LEFT, padx=(0, 18), anchor=tk.N)
        self._set_art(None)

        info = tk.Frame(inner, bg=self.CARD_BG)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        top_line = tk.Frame(info, bg=self.CARD_BG)
        top_line.pack(fill=tk.X)
        tk.Label(top_line, text="NOW PLAYING", font=(self.FONT, 9, "bold"),
                 bg=self.CARD_BG, fg=self.TEXT_SECONDARY).pack(side=tk.LEFT)
        self.source_label = tk.Label(top_line, text="", font=(self.FONT, 8, "bold"),
                                     bg=self.CARD_BG, fg=self.ACCENT_BLUE)
        self.source_label.pack(side=tk.LEFT, padx=(8, 0))

        self.song_label = tk.Label(info, text="Not playing",
                                   font=(self.FONT, 19, "bold"), bg=self.CARD_BG,
                                   fg=self.TEXT_PRIMARY, wraplength=600, justify=tk.LEFT, anchor=tk.W)
        self.song_label.pack(anchor=tk.W, pady=(6, 2), fill=tk.X)

        self.artist_label = tk.Label(info, text="\u2013", font=(self.FONT, 13),
                                     bg=self.CARD_BG, fg=self.TEXT_SECONDARY, anchor=tk.W,
                                     cursor="hand2")
        self.artist_label.pack(anchor=tk.W, fill=tk.X)
        self.artist_label.bind("<Button-1>", lambda e: self._open_artist_from_label())

        prog_row = tk.Frame(info, bg=self.CARD_BG)
        prog_row.pack(fill=tk.X, pady=(12, 0))
        self.elapsed_label = tk.Label(prog_row, text="0:00", font=(self.FONT, 8),
                                      bg=self.CARD_BG, fg=self.TEXT_MUTED, width=5)
        self.elapsed_label.pack(side=tk.LEFT)
        self.progress_bar = ttk.Progressbar(prog_row, style="Spotify.Horizontal.TProgressbar",
                                            orient=tk.HORIZONTAL, mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.duration_label = tk.Label(prog_row, text="0:00", font=(self.FONT, 8),
                                       bg=self.CARD_BG, fg=self.TEXT_MUTED, width=5)
        self.duration_label.pack(side=tk.LEFT)

        bottom_row = tk.Frame(info, bg=self.CARD_BG)
        bottom_row.pack(fill=tk.X, pady=(14, 0))

        self.controls_frame = tk.Frame(bottom_row, bg=self.CARD_BG)
        self.controls_frame.pack(side=tk.LEFT)

        self.btn_prev    = self._ctrl_btn(self.controls_frame, "\u23ee", "previous")
        self.btn_play    = self._ctrl_btn(self.controls_frame, "\u25b6", "play_pause", wide=True)
        self.btn_next    = self._ctrl_btn(self.controls_frame, "\u23ed", "next")
        self.btn_shuffle = self._ctrl_btn(self.controls_frame, "\U0001F500", "toggle_shuffle")
        self.btn_repeat  = self._ctrl_btn(self.controls_frame, "\U0001F501", "cycle_repeat")

        status_col = tk.Frame(bottom_row, bg=self.CARD_BG)
        status_col.pack(side=tk.RIGHT, anchor=tk.E)
        self.status_label = tk.Label(status_col, text="", font=(self.FONT, 9, "bold"),
                                     bg=self.CARD_BG, fg=self.ACCENT_GREEN, anchor=tk.E)
        self.status_label.pack(anchor=tk.E)
        self.reconnect_button = tk.Button(
            status_col, text="Reconnect Spotify", font=(self.FONT, 8, "bold"),
            bg=self.ACCENT_BLUE, fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=8, pady=3,
            command=lambda: self._emit("reconnect"))

        self.playcount_label = tk.Label(info, text="Plays: \u2013 | Time: \u2013",
                                        font=(self.FONT, 9), bg=self.CARD_BG,
                                        fg=self.TEXT_SECONDARY, anchor=tk.W)
        self.playcount_label.pack(anchor=tk.W, pady=(10, 0), fill=tk.X)

        self._current_artist_name = None

    def _ctrl_btn(self, parent, label, action, wide=False):
        b = tk.Button(parent, text=label, font=(self.FONT, 11),
                      bg=self.DARK_BG, fg=self.TEXT_PRIMARY,
                      activebackground=self.ACCENT_GREEN, activeforeground=self.TEXT_PRIMARY,
                      relief=tk.FLAT, padx=(14 if wide else 10), pady=6,
                      command=lambda: self._emit(action))
        b.pack(side=tk.LEFT, padx=(0, 6))
        return b

    def _open_artist_from_label(self):
        if self._current_artist_name:
            self.open_artist_detail(self._current_artist_name)

    # -- Totals strip ------------------------------------------------------
    def _build_totals(self, parent):
        self.totals_frame = tk.Frame(parent, bg=self.CARD_BG)
        self.totals_frame.pack(fill=tk.X, pady=(14, 0))

        inner = tk.Frame(self.totals_frame, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=10, pady=14)

        self.lbl_plays,   _ = self._stat_card(inner, "TOTAL PLAYS",       "plays")
        self.lbl_minutes, _ = self._stat_card(inner, "MINUTES LISTENED",  "minutes")
        self.lbl_artists, _ = self._stat_card(inner, "UNIQUE ARTISTS",    "artists")

    def _stat_card(self, parent, label_text, kind):
        card = tk.Frame(parent, bg=self.CARD_BG, cursor="hand2")
        card.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10)
        val = tk.Label(card, text="0", font=(self.FONT, 22, "bold"),
                       bg=self.CARD_BG, fg=self.TEXT_PRIMARY, cursor="hand2")
        val.pack(anchor=tk.W)
        lbl = tk.Label(card, text=f"{label_text}  \u2197", font=(self.FONT, 8, "bold"),
                       bg=self.CARD_BG, fg=self.TEXT_SECONDARY, cursor="hand2")
        lbl.pack(anchor=tk.W)

        # Any click on the card (frame, value, or label) opens the breakdown.
        for widget in (card, val, lbl):
            widget.bind("<Button-1>", lambda e, k=kind: self.open_stats_breakdown(k))

        # Subtle hover feedback so it reads as clickable.
        def _on_enter(_e): val.config(fg=self.ACCENT_GREEN)
        def _on_leave(_e): val.config(fg=self.TEXT_PRIMARY)
        for widget in (card, val, lbl):
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)

        return val, lbl

    # -- Top Songs tab -------------------------------------------------------
    def _build_songs_tab(self, parent):
        frame = tk.Frame(parent, bg=self.CARD_BG)

        hdr = tk.Frame(frame, bg=self.CARD_BG)
        hdr.pack(fill=tk.X, padx=15, pady=(15, 8))
        tk.Label(hdr, text="Your most played songs  (double-click for song details)", font=(self.FONT, 10),
                 bg=self.CARD_BG, fg=self.TEXT_SECONDARY).pack(side=tk.LEFT)
        tk.Label(hdr, text="\U0001F50E", bg=self.CARD_BG, fg=self.TEXT_MUTED).pack(side=tk.RIGHT)
        tk.Entry(hdr, textvariable=self._songs_filter, font=(self.FONT, 9), bg=self.CARD_BG_2,
                 fg=self.TEXT_PRIMARY, insertbackground=self.TEXT_PRIMARY,
                 relief=tk.FLAT, width=18).pack(side=tk.RIGHT, padx=(0, 4))

        list_frame = tk.Frame(frame, bg=self.CARD_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))

        cols = ("title", "artist", "plays", "time")
        tree = ttk.Treeview(list_frame, columns=cols, show="tree headings",
                            style="Dark.Treeview", selectmode="browse")
        tree.heading("#0", text="")
        tree.column("#0", width=44, anchor=tk.CENTER, stretch=False)
        headings = {"title": "Title", "artist": "Artist", "plays": "Plays", "time": "Time"}
        for col in cols:
            anchor = tk.W if col in ("title", "artist") else tk.CENTER
            width  = 220 if col in ("title", "artist") else 70
            tree.heading(col, text=headings[col], anchor=anchor,
                         command=lambda c=col: self._on_sort("songs", c))
            tree.column(col, width=width, anchor=anchor, stretch=(col in ("title", "artist")))

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("odd",  background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)
        tree.bind("<Double-1>", lambda e: self._on_song_row_activated())

        self.songs_tree = tree
        return frame

    def _on_song_row_activated(self):
        sel = self.songs_tree.selection()
        if not sel:
            return
        values = self.songs_tree.item(sel[0], "values")
        if len(values) >= 2:
            self.open_song_detail(values[0], values[1])

    # -- Top Artists tab ------------------------------------------------------
    def _build_artists_tab(self, parent):
        frame = tk.Frame(parent, bg=self.CARD_BG)

        hdr = tk.Frame(frame, bg=self.CARD_BG)
        hdr.pack(fill=tk.X, padx=15, pady=(15, 8))
        tk.Label(hdr, text="Your most played artists  (double-click for details)",
                 font=(self.FONT, 10), bg=self.CARD_BG, fg=self.TEXT_SECONDARY).pack(side=tk.LEFT)
        tk.Label(hdr, text="\U0001F50E", bg=self.CARD_BG, fg=self.TEXT_MUTED).pack(side=tk.RIGHT)
        tk.Entry(hdr, textvariable=self._artists_filter, font=(self.FONT, 9), bg=self.CARD_BG_2,
                 fg=self.TEXT_PRIMARY, insertbackground=self.TEXT_PRIMARY,
                 relief=tk.FLAT, width=18).pack(side=tk.RIGHT, padx=(0, 4))

        list_frame = tk.Frame(frame, bg=self.CARD_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))

        cols = ("name", "plays", "time")
        tree = ttk.Treeview(list_frame, columns=cols, show="tree headings",
                            style="Dark.Treeview", selectmode="browse")
        tree.heading("#0", text="")
        tree.column("#0", width=44, anchor=tk.CENTER, stretch=False)
        headings = {"name": "Artist", "plays": "Plays", "time": "Time"}
        for col in cols:
            anchor = tk.W if col == "name" else tk.CENTER
            width  = 260 if col == "name" else 90
            tree.heading(col, text=headings[col], anchor=anchor,
                         command=lambda c=col: self._on_sort("artists", c))
            tree.column(col, width=width, anchor=anchor, stretch=(col == "name"))

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("odd",  background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)
        tree.bind("<Double-1>", lambda e: self._on_artist_row_activated())

        self.artists_tree = tree
        return frame

    def _on_artist_row_activated(self):
        sel = self.artists_tree.selection()
        if not sel:
            return
        values = self.artists_tree.item(sel[0], "values")
        if values:
            self.open_artist_detail(values[0])

    # -- History tab ------------------------------------------------------
    def _build_history_tab(self, parent):
        frame = tk.Frame(parent, bg=self.CARD_BG)

        hdr = tk.Frame(frame, bg=self.CARD_BG)
        hdr.pack(fill=tk.X, padx=15, pady=(15, 8))
        tk.Label(hdr, text="Every play, most recent first",
                 font=(self.FONT, 10), bg=self.CARD_BG, fg=self.TEXT_SECONDARY).pack(side=tk.LEFT)

        tk.Button(hdr, text="\u21bb Refresh", font=(self.FONT, 8), bg=self.CARD_BG_2,
                  fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=8, pady=3,
                  command=lambda: self._load_history_page(reset=True)
                  ).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Label(hdr, text="\U0001F50E", bg=self.CARD_BG, fg=self.TEXT_MUTED).pack(side=tk.RIGHT)
        tk.Entry(hdr, textvariable=self._history_search, font=(self.FONT, 9), bg=self.CARD_BG_2,
                 fg=self.TEXT_PRIMARY, insertbackground=self.TEXT_PRIMARY,
                 relief=tk.FLAT, width=18).pack(side=tk.RIGHT, padx=(0, 4))

        list_frame = tk.Frame(frame, bg=self.CARD_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 8))

        cols = ("when", "title", "artist", "duration", "source")
        tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                            style="Dark.Treeview", selectmode="browse")
        headings = {"when": "Played At", "title": "Title", "artist": "Artist",
                    "duration": "Time", "source": "Source"}
        widths   = {"when": 150, "title": 220, "artist": 180, "duration": 70, "source": 70}
        for col in cols:
            anchor = tk.W if col in ("title", "artist") else tk.CENTER
            tree.heading(col, text=headings[col], anchor=anchor)
            tree.column(col, width=widths[col], anchor=anchor, stretch=(col in ("title", "artist")))

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("odd",  background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)
        tree.bind("<Double-1>", lambda e: self._on_history_row_activated())
        self.history_tree = tree

        pager = tk.Frame(frame, bg=self.CARD_BG)
        pager.pack(fill=tk.X, padx=15, pady=(0, 15))
        self.hist_prev_btn = tk.Button(pager, text="\u25c0 Prev", font=(self.FONT, 8),
                                       bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                                       padx=10, pady=4, command=self._history_prev_page)
        self.hist_prev_btn.pack(side=tk.LEFT)
        self.hist_page_label = tk.Label(pager, text="Page 1 of 1", font=(self.FONT, 9),
                                        bg=self.CARD_BG, fg=self.TEXT_SECONDARY)
        self.hist_page_label.pack(side=tk.LEFT, padx=12)
        self.hist_next_btn = tk.Button(pager, text="Next \u25b6", font=(self.FONT, 8),
                                       bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                                       padx=10, pady=4, command=self._history_next_page)
        self.hist_next_btn.pack(side=tk.LEFT)

        return frame

    def _on_history_row_activated(self):
        sel = self.history_tree.selection()
        if not sel:
            return
        values = self.history_tree.item(sel[0], "values")
        if len(values) >= 3:
            self.open_song_detail(values[1], values[2])

    def _history_prev_page(self):
        if self._history_page > 0:
            self._history_page -= 1
            self._load_history_page(reset=False)

    def _history_next_page(self):
        max_page = max(0, (self._history_total - 1) // self.HIST_PAGE_SIZE)
        if self._history_page < max_page:
            self._history_page += 1
            self._load_history_page(reset=False)

    def _load_history_page(self, reset=False):
        if reset:
            self._history_page = 0
        search = self._history_search.get().strip() or None
        try:
            self._history_total = self._db.get_listen_history_count(search=search)
            rows = self._db.get_listen_history(
                limit=self.HIST_PAGE_SIZE,
                offset=self._history_page * self.HIST_PAGE_SIZE,
                search=search,
            )
        except Exception as exc:
            print(f"[UI] History load error: {exc}")
            rows = []
            self._history_total = 0

        self.history_tree.delete(*self.history_tree.get_children())
        for i, row in enumerate(rows):
            _id, title, artist, duration, played_at, source = tuple(row)
            tag = "even" if i % 2 else "odd"
            self.history_tree.insert(
                "", tk.END,
                values=(played_at or "\u2013", title, artist, self._fmt_dur(duration), source),
                tags=(tag,),
            )

        max_page = max(0, (self._history_total - 1) // self.HIST_PAGE_SIZE) if self._history_total else 0
        self.hist_page_label.config(text=f"Page {self._history_page + 1} of {max_page + 1}  ({self._history_total:,} plays)")
        self.hist_prev_btn.config(state=tk.NORMAL if self._history_page > 0 else tk.DISABLED)
        self.hist_next_btn.config(state=tk.NORMAL if self._history_page < max_page else tk.DISABLED)

    def notify_history_changed(self):
        """Call after a new play is recorded so the History tab stays live
        if the user currently has it open and is on page 1."""
        try:
            if self.notebook.tab(self.notebook.select(), "text") == "History" and self._history_page == 0:
                self._load_history_page(reset=False)
        except Exception:
            pass

    # -- Import tab -------------------------------------------------------
    def _build_import_tab(self, parent):
        frame = tk.Frame(parent, bg=self.CARD_BG)
        inner = tk.Frame(frame, bg=self.CARD_BG)
        inner.pack(fill=tk.BOTH, expand=True, padx=25, pady=25)

        tk.Label(inner, text="Import your Spotify Data Export",
                 font=(self.FONT, 13, "bold"), bg=self.CARD_BG, fg=self.TEXT_PRIMARY
                 ).pack(anchor=tk.W)

        tk.Label(
            inner,
            text=(
                "Spotify lets you request a full history of everything you've ever\n"
                "streamed. Go to spotify.com \u2192 Account \u2192 Privacy Settings \u2192 request\n"
                "your \u201cExtended streaming history\u201d. It arrives as a .zip within a\n"
                "few days \u2014 unzip it, then select the JSON files below (they're\n"
                "usually named Streaming_History_Audio_*.json)."
            ),
            font=(self.FONT, 9), justify=tk.LEFT, bg=self.CARD_BG, fg=self.TEXT_SECONDARY,
        ).pack(anchor=tk.W, pady=(8, 18))

        self.import_button = tk.Button(
            inner, text="Choose JSON Files\u2026", font=(self.FONT, 10, "bold"),
            bg=self.ACCENT_GREEN, fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=16, pady=8,
            command=self._choose_import_files,
        )
        self.import_button.pack(anchor=tk.W)

        self.import_progress = ttk.Progressbar(
            inner, style="Spotify.Horizontal.TProgressbar",
            orient=tk.HORIZONTAL, mode="determinate", length=400,
        )
        self.import_progress.pack(anchor=tk.W, pady=(16, 6), fill=tk.X)

        self.import_status_label = tk.Label(
            inner, text="", font=(self.FONT, 9), bg=self.CARD_BG, fg=self.TEXT_SECONDARY,
        )
        self.import_status_label.pack(anchor=tk.W)

        self.import_result_text = tk.Text(
            inner, height=8, font=("Consolas", 9), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
            relief=tk.FLAT, wrap=tk.WORD, state=tk.DISABLED,
        )
        self.import_result_text.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        return frame

    def _choose_import_files(self):
        if self._import_running:
            return
        paths = filedialog.askopenfilenames(
            title="Select Spotify export JSON files",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not paths:
            return
        self._run_import(list(paths))

    def _run_import(self, paths):
        self._import_running = True
        self.import_button.config(state=tk.DISABLED)
        self.import_progress.configure(value=0, maximum=max(1, len(paths)))
        self.import_status_label.config(text=f"Importing 0 / {len(paths)} files\u2026")
        self._set_import_result_text("")

        def progress_cb(files_done, files_total, imported_so_far):
            self.root.after(0, lambda: self._update_import_progress(files_done, files_total, imported_so_far))

        def worker():
            try:
                result = self._db.import_spotify_export(paths, progress_callback=progress_cb)
            except Exception as exc:
                result = {"imported": 0, "skipped": 0, "files": 0, "songs": 0, "artists": 0, "errors": [str(exc)]}
            self.root.after(0, lambda: self._finish_import(result))

        threading.Thread(target=worker, daemon=True).start()

    def _update_import_progress(self, files_done, files_total, imported_so_far):
        self.import_progress.configure(value=files_done, maximum=max(1, files_total))
        self.import_status_label.config(
            text=f"Importing {files_done} / {files_total} files\u2026  ({imported_so_far:,} plays so far)"
        )

    def _finish_import(self, result):
        self._import_running = False
        self.import_button.config(state=tk.NORMAL)
        self.import_status_label.config(text="Import complete.")

        lines = [
            f"Files processed : {result.get('files', 0)}",
            f"Plays imported  : {result.get('imported', 0):,}",
            f"Rows skipped    : {result.get('skipped', 0):,}  (under 15s or missing data)",
            f"New/updated songs   : {result.get('songs', 0):,}",
            f"New/updated artists : {result.get('artists', 0):,}",
        ]
        errors = result.get("errors") or []
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  \u2022 {e}" for e in errors[:10])
        self._set_import_result_text("\n".join(lines))

        # Refresh everything that could have changed
        try:
            self.update_top_songs(self._db.get_all_songs(limit=25))
            self.update_top_artists(self._db.get_all_artists(limit=25))
            self.update_totals(
                self._db.get_total_songs(), self._db.get_total_minutes(), self._db.get_total_artists()
            )
            self._load_history_page(reset=True)
        except Exception as exc:
            print(f"[UI] Post-import refresh error: {exc}")

        if result.get("imported", 0) > 0:
            messagebox.showinfo("Import Complete",
                                f"Imported {result['imported']:,} plays from {result.get('files',0)} file(s).")
        elif errors:
            messagebox.showerror("Import Failed", "\n".join(errors[:5]))

    def _set_import_result_text(self, text):
        self.import_result_text.config(state=tk.NORMAL)
        self.import_result_text.delete("1.0", tk.END)
        self.import_result_text.insert("1.0", text)
        self.import_result_text.config(state=tk.DISABLED)

    # -- Settings panel ----------------------------------------------------
    def _toggle_settings(self):
        if self.settings_menu and self.settings_menu.winfo_exists():
            self.settings_menu.destroy()
            self.settings_menu = None
            return

        menu = tk.Toplevel(self.root)
        menu.title("Settings")
        menu.resizable(False, False)
        menu.configure(bg=self.CARD_BG)
        menu.attributes("-topmost", True)
        x = max(0, self.settings_button.winfo_rootx() - 220)
        y = max(0, self.settings_button.winfo_rooty() + 34)
        menu.geometry(f"310x540+{x}+{y}")
        self.settings_menu = menu

        panel = tk.Frame(menu, bg=self.CARD_BG)
        panel.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        def _sect(text):
            tk.Label(panel, text=text, font=(self.FONT, 10, "bold"),
                     bg=self.CARD_BG, fg=self.TEXT_PRIMARY).pack(anchor=tk.W, pady=(8, 4))

        _sect("Spotify Account")
        tk.Button(panel, text="Connect Spotify Account",
                  font=(self.FONT, 9, "bold"), bg=self.ACCENT_GREEN, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, command=lambda: self._emit("reconnect")
                  ).pack(anchor=tk.W, fill=tk.X, pady=(0, 4))
        tk.Button(panel, text="Disconnect Spotify Account",
                  font=(self.FONT, 9), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, command=lambda: self._emit("forget_account")
                  ).pack(anchor=tk.W, fill=tk.X, pady=(0, 4))

        _sect("Show / Hide")
        self._toggle_row(panel, "Playback Controls", self.show_controls)
        self._toggle_row(panel, "Totals Strip",       self.show_totals)

        _sect("Window")
        self._toggle_row(panel, "Always on Top", self.always_on_top,
                         cmd=lambda: self.root.attributes("-topmost", self.always_on_top.get()))

        _sect("Crossfade Compensation")
        exact_source = self._last_source in ("local", "remote")
        cf_note_text = (
            "Disabled \u2014 exact timing available via Spotify account."
            if exact_source else
            "Match this to Spotify's crossfade setting.\n"
            "Added to each song's time when the title flips early."
        )
        tk.Label(panel, text=cf_note_text, font=(self.FONT, 8), bg=self.CARD_BG,
                 fg=self.TEXT_MUTED, justify=tk.LEFT, wraplength=270
                 ).pack(anchor=tk.W, pady=(0, 4))

        cf_row = tk.Frame(panel, bg=self.CARD_BG)
        cf_row.pack(fill=tk.X)
        cf_val_lbl = tk.Label(cf_row, text=f"{self.crossfade_var.get()}s",
                              font=(self.FONT, 9, "bold"), bg=self.CARD_BG,
                              fg=self.TEXT_PRIMARY, width=4)
        cf_val_lbl.pack(side=tk.RIGHT)

        def _on_cf_change(val):
            v = int(float(val))
            cf_val_lbl.config(text=f"{v}s")
            self._emit("set_crossfade", v)

        cf_slider = tk.Scale(
            cf_row, from_=0, to=12, orient=tk.HORIZONTAL,
            variable=self.crossfade_var, command=_on_cf_change,
            bg=self.CARD_BG, fg=self.TEXT_PRIMARY,
            troughcolor=self.CARD_BG_2, highlightthickness=0,
            activebackground=self.ACCENT_GREEN, sliderrelief=tk.FLAT,
            showvalue=False, length=200,
        )
        cf_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._crossfade_slider = cf_slider
        if exact_source:
            cf_slider.config(state=tk.DISABLED, fg=self.TEXT_MUTED, troughcolor=self.CARD_BG)

        _sect("Data")
        tk.Button(panel, text="Edit Database\u2026",
                  font=(self.FONT, 9, "bold"), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, command=self.open_database_editor
                  ).pack(anchor=tk.W, fill=tk.X, pady=(0, 4))

        menu.protocol("WM_DELETE_WINDOW", self._toggle_settings)

    def _toggle_row(self, parent, label, var, cmd=None):
        tk.Checkbutton(parent, text=label, variable=var, font=(self.FONT, 9),
                       bg=self.CARD_BG, fg=self.TEXT_PRIMARY,
                       activebackground=self.CARD_BG, activeforeground=self.TEXT_PRIMARY,
                       selectcolor=self.DARK_BG,
                       command=cmd or self._apply_visibility).pack(anchor=tk.W, pady=2)

    def _apply_visibility(self):
        if self.show_controls.get():
            if not self.controls_frame.winfo_manager():
                self.controls_frame.pack(side=tk.LEFT)
        else:
            self.controls_frame.pack_forget()

        if self.show_totals.get():
            self.totals_frame.pack(fill=tk.X, pady=(14, 0))
        else:
            self.totals_frame.pack_forget()

    def apply_visibility_settings(self):
        self._apply_visibility()

    def _refresh_crossfade_slider_state(self):
        slider = self._crossfade_slider
        if slider is None:
            return
        try:
            if not slider.winfo_exists():
                self._crossfade_slider = None
                return
        except Exception:
            return
        exact = self._last_source in ("local", "remote")
        slider.config(
            state=tk.DISABLED if exact else tk.NORMAL,
            fg=self.TEXT_MUTED if exact else self.TEXT_PRIMARY,
            troughcolor=self.CARD_BG if exact else self.CARD_BG_2,
        )

    # -- Data update methods (called from main.py on the Tk thread) --------
    def update_current_track(self, song, artist, plays, duration,
                             playback=None, playback_state="idle"):
        playback = playback or {}
        self._set_art(playback.get("thumbnail"))
        self._current_artist_name = artist

        new_source = playback.get("source")
        if new_source and new_source != self._last_source:
            self._last_source = new_source
            self._refresh_crossfade_slider_state()

        if playback_state == "idle":
            self.song_label.config(text="Not playing", fg=self.TEXT_PRIMARY)
            self.artist_label.config(text="\u2013", fg=self.TEXT_SECONDARY)
            self.playcount_label.config(text="Plays: \u2013 | Time: \u2013")
            self.status_label.config(text="", fg=self.TEXT_SECONDARY)
        else:
            dim = playback_state in ("paused_closed", "disconnected")
            fg  = self.TEXT_MUTED if dim else self.TEXT_PRIMARY
            self.song_label.config(text=song or "Unknown", fg=fg)
            self.artist_label.config(text=artist or "\u2013", fg=self.TEXT_MUTED if dim else self.TEXT_SECONDARY)
            self.playcount_label.config(text=f"Plays: {plays} | Time: {self._fmt_dur(duration)}")

            if playback_state == "disconnected":
                self.status_label.config(text="Disconnected", fg=self.ACCENT_RED)
            elif playback_state == "paused_closed":
                self.status_label.config(text="Paused", fg=self.TEXT_MUTED)
            else:
                self.status_label.config(text="Playing", fg=self.ACCENT_GREEN)

        src_txt = {"local": "\U0001F4BB DESKTOP", "remote": "\U0001F4F1 REMOTE",
                   "window_title": "\U0001FA9F WINDOW"}.get(playback.get("source"), "")
        self.source_label.config(text=src_txt)

        self._update_progress(playback, playback_state)
        self._update_controls(playback, playback_state)

    def _update_progress(self, pb, state):
        pos = float(pb.get("position_sec") or 0.0)
        dur = float(pb.get("duration_sec") or 0.0)
        if state == "idle" or dur <= 0:
            self.progress_bar.configure(value=0, maximum=1)
            self.elapsed_label.config(text="0:00")
            self.duration_label.config(text="0:00")
            return
        self.progress_bar.configure(maximum=dur, value=min(pos, dur))
        self.elapsed_label.config(text=self._fmt_clock(pos))
        self.duration_label.config(text=self._fmt_clock(dur))

    def _update_controls(self, pb, state):
        is_playing = bool(pb.get("is_playing"))
        can_pp     = pb.get("can_play_pause") or state == "paused_closed"

        self.btn_play.config(state=tk.NORMAL if can_pp else tk.DISABLED,
                             text="\u23f8" if is_playing else "\u25b6")
        self.btn_prev.config(state=tk.NORMAL if pb.get("can_previous") else tk.DISABLED)
        self.btn_next.config(state=tk.NORMAL if pb.get("can_next")     else tk.DISABLED)

        shuffle_on  = bool(pb.get("shuffle_active"))
        repeat_mode = pb.get("repeat_mode") or "off"
        self.btn_shuffle.config(
            state=tk.NORMAL if pb.get("can_shuffle") else tk.DISABLED,
            bg=self.ACCENT_GREEN if shuffle_on else self.DARK_BG)
        self.btn_repeat.config(
            state=tk.NORMAL if pb.get("can_repeat") else tk.DISABLED,
            bg=self.ACCENT_GREEN if repeat_mode != "off" else self.DARK_BG,
            text="\U0001F502" if repeat_mode == "track" else "\U0001F501")

        if pb.get("connected", True):
            self.reconnect_button.pack_forget()
        else:
            self.reconnect_button.pack(anchor=tk.E, pady=(4, 0))

    def update_totals(self, total_songs, total_minutes, total_artists):
        self.lbl_plays.config(text=f"{total_songs:,}")
        self.lbl_minutes.config(text=f"{total_minutes:,}")
        self.lbl_artists.config(text=f"{total_artists:,}")

    def update_top_songs(self, songs):
        self._songs_data = list(songs or [])
        self._render_songs_table()

    def update_top_artists(self, artists):
        self._artists_data = list(artists or [])
        self._render_artists_table()

    # -- Table rendering ----------------------------------------------------
    def _on_sort(self, kind, col):
        current = self._songs_sort if kind == "songs" else self._artists_sort
        reverse = not current[1] if current[0] == col else (col in ("plays", "time"))
        if kind == "songs":
            self._songs_sort = (col, reverse)
            self._render_songs_table()
        else:
            self._artists_sort = (col, reverse)
            self._render_artists_table()

    def _row_field(self, row, index, default=None):
        try:
            return row[index]
        except (IndexError, KeyError):
            return default

    def _render_songs_table(self):
        tree = self.songs_tree
        tree.delete(*tree.get_children())
        needle = self._songs_filter.get().strip().lower()
        rows = [r for r in self._songs_data
                if not needle or needle in r[0].lower() or needle in r[1].lower()]
        key_fn = {"title": lambda r: r[0].lower(), "artist": lambda r: r[1].lower(),
                  "plays": lambda r: r[2],          "time":   lambda r: r[3]}
        col, rev = self._songs_sort
        rows.sort(key=key_fn.get(col, key_fn["plays"]), reverse=rev)

        for i, row in enumerate(rows):
            title, artist, plays, dur = row[0], row[1], row[2], row[3]
            art_url = self._row_field(row, 5)
            icon = self._get_song_icon((title, artist), art_url)
            tree.insert("", tk.END, image=icon,
                        values=(title, artist, plays, self._fmt_dur(dur)),
                        tags=("even" if i % 2 else "odd",))

    def _render_artists_table(self):
        tree = self.artists_tree
        tree.delete(*tree.get_children())
        needle = self._artists_filter.get().strip().lower()
        rows = [r for r in self._artists_data if not needle or needle in r[0].lower()]
        key_fn = {"name": lambda r: r[0].lower(), "plays": lambda r: r[1], "time": lambda r: r[2]}
        col, rev = self._artists_sort
        rows.sort(key=key_fn.get(col, key_fn["plays"]), reverse=rev)

        for i, row in enumerate(rows):
            name, plays, dur = row[0], row[1], row[2]
            img_url = self._row_field(row, 4)
            icon = self._get_artist_icon(name, img_url)
            tree.insert("", tk.END, image=icon,
                        values=(name, plays, self._fmt_dur(dur)),
                        tags=("even" if i % 2 else "odd",))

    # -- Small row icons (album art / artist avatar) -----------------------
    def _get_song_icon(self, key, url):
        if key in self._song_icon_cache:
            return self._song_icon_cache[key]
        if url and key not in self._icon_fetch_inflight:
            self._icon_fetch_inflight.add(key)
            self._fetch_icon_async(url, lambda data: self._on_song_icon_ready(key, data))
        return self._placeholder_song_icon

    def _get_artist_icon(self, name, url):
        if name in self._artist_icon_cache:
            return self._artist_icon_cache[name]
        if url and name not in self._icon_fetch_inflight:
            self._icon_fetch_inflight.add(name)
            self._fetch_icon_async(url, lambda data: self._on_artist_icon_ready(name, data))
        return self._placeholder_artist_icon

    def _fetch_icon_async(self, url, on_done):
        def worker():
            data = self._images.get_bytes(url)
            self.root.after(0, lambda: on_done(data))
        threading.Thread(target=worker, daemon=True).start()

    def _on_song_icon_ready(self, key, data):
        self._icon_fetch_inflight.discard(key)
        photo = self._bytes_to_icon(data)
        if photo is None:
            return
        self._song_icon_cache[key] = photo
        self._render_songs_table()

    def _on_artist_icon_ready(self, name, data):
        self._icon_fetch_inflight.discard(name)
        photo = self._bytes_to_icon(data)
        if photo is None:
            return
        self._artist_icon_cache[name] = photo
        self._render_artists_table()

    def _bytes_to_icon(self, data):
        if not data:
            return None
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            side = min(img.size)
            l = (img.size[0] - side) // 2
            t = (img.size[1] - side) // 2
            img = img.crop((l, t, l + side, t + side)).resize(
                (self.ICON_SIZE, self.ICON_SIZE), Image.LANCZOS)
            img = self._rounded(img, radius=self.ICON_SIZE // 5)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # -- Artist detail popup -------------------------------------------------
    def open_artist_detail(self, artist_name):
        if not artist_name:
            return
        win = tk.Toplevel(self.root)
        win.title(artist_name)
        win.geometry("560x620")
        win.configure(bg=self.CARD_BG)

        header = tk.Frame(win, bg=self.CARD_BG)
        header.pack(fill=tk.X, padx=20, pady=20)

        art_label = tk.Label(header, bg=self.CARD_BG)
        art_label.pack(side=tk.LEFT, padx=(0, 16))
        placeholder = ImageTk.PhotoImage(self._make_avatar_icon(96))
        art_label.image = placeholder
        art_label.configure(image=placeholder)

        info = tk.Frame(header, bg=self.CARD_BG)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(info, text=artist_name, font=(self.FONT, 16, "bold"),
                 bg=self.CARD_BG, fg=self.TEXT_PRIMARY, wraplength=380, justify=tk.LEFT
                 ).pack(anchor=tk.W)
        stats_label = tk.Label(info, text="Loading\u2026", font=(self.FONT, 9),
                               bg=self.CARD_BG, fg=self.TEXT_SECONDARY, justify=tk.LEFT)
        stats_label.pack(anchor=tk.W, pady=(6, 0))

        songs_frame = tk.Frame(win, bg=self.CARD_BG)
        songs_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        tk.Label(songs_frame, text="Songs", font=(self.FONT, 10, "bold"),
                 bg=self.CARD_BG, fg=self.ACCENT_GREEN).pack(anchor=tk.W, pady=(0, 6))

        cols = ("title", "plays", "time")
        tree = ttk.Treeview(songs_frame, columns=cols, show="headings",
                            style="Dark.Treeview", selectmode="browse")
        tree.heading("title", text="Title", anchor=tk.W)
        tree.heading("plays", text="Plays", anchor=tk.CENTER)
        tree.heading("time",  text="Time",  anchor=tk.CENTER)
        tree.column("title", width=280, anchor=tk.W, stretch=True)
        tree.column("plays", width=70,  anchor=tk.CENTER, stretch=False)
        tree.column("time",  width=90,  anchor=tk.CENTER, stretch=False)
        vsb = ttk.Scrollbar(songs_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("odd",  background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)

        # Populate (DB reads are fast/local; safe to do inline)
        try:
            artist_row = self._db.get_artist_stats(artist_name)
            songs = self._db.get_songs_by_artist(artist_name)
            first_played, last_played = self._db.get_artist_first_last_played(artist_name)
        except Exception as exc:
            print(f"[UI] Artist detail load error: {exc}")
            artist_row = None
            songs = []
            first_played = last_played = None

        if artist_row:
            plays = artist_row[1]
            minutes = (artist_row[2] or 0) // 60
            image_url = self._row_field(tuple(artist_row), 4)
        else:
            plays = sum(r[2] for r in songs)
            minutes = sum(r[3] for r in songs) // 60
            image_url = None

        stats_lines = [f"{plays:,} plays  \u2022  {minutes:,} minutes listened"]
        if first_played:
            stats_lines.append(f"First played: {first_played}")
        if last_played:
            stats_lines.append(f"Last played:  {last_played}")
        stats_label.config(text="\n".join(stats_lines))

        for i, row in enumerate(songs):
            title, _artist, song_plays, dur = row[0], row[1], row[2], row[3]
            tag = "even" if i % 2 else "odd"
            tree.insert("", tk.END, values=(title, song_plays, self._fmt_dur(dur)), tags=(tag,))

        if not songs:
            tk.Label(songs_frame, text="No songs recorded yet for this artist.",
                     font=(self.FONT, 9), bg=self.CARD_BG, fg=self.TEXT_MUTED
                     ).pack(anchor=tk.W, pady=8)

        # Kick off an async artist-image fetch if we have a Spotify account
        # connected; updates the popup in place when it arrives.
        if image_url:
            def apply(data):
                img = self._bytes_to_round_photo(data, 96)
                if img:
                    art_label.image = img
                    art_label.configure(image=img)
            self._fetch_icon_async(image_url, apply)
        else:
            self._emit("fetch_artist_image", {
                "artist": artist_name,
                "callback": lambda url: self._apply_fetched_artist_image(url, art_label),
            })

        tk.Button(win, text="Close", font=(self.FONT, 9), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, padx=12, pady=5, command=win.destroy).pack(pady=(0, 16))

    def _apply_fetched_artist_image(self, url, art_label):
        if not url:
            return
        def apply(data):
            try:
                if not art_label.winfo_exists():
                    return
            except Exception:
                return
            img = self._bytes_to_round_photo(data, 96)
            if img:
                art_label.image = img
                art_label.configure(image=img)
        self._fetch_icon_async(url, apply)

    def _bytes_to_round_photo(self, data, size):
        if not data:
            return None
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            side = min(img.size)
            l = (img.size[0] - side) // 2
            t = (img.size[1] - side) // 2
            img = img.crop((l, t, l + side, t + side)).resize((size, size), Image.LANCZOS)
            img = self._rounded(img, radius=size // 6)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # -- Bar chart rendering (no matplotlib dependency -- drawn with PIL) ----
    def _render_bar_chart(self, pairs, width=560, height=260, value_fmt=None, color=None):
        """pairs: list of (label:str, value:number), oldest/first to newest/last.
        Returns a PIL Image. Values are drawn as vertical bars with the value
        printed above each bar and the label printed (rotated if crowded)
        below it."""
        color = color or self.ACCENT_GREEN
        img = Image.new("RGB", (width, height), self._hex_to_rgb(self.CARD_BG_2))
        draw = ImageDraw.Draw(img)

        if not pairs:
            draw.text((width // 2 - 60, height // 2 - 8), "No data yet",
                      fill=self._hex_to_rgb(self.TEXT_MUTED))
            return img

        margin_left, margin_right = 20, 20
        margin_top, margin_bottom = 30, 40
        chart_w = width - margin_left - margin_right
        chart_h = height - margin_top - margin_bottom

        values = [v for _, v in pairs]
        max_val = max(values) if values else 1
        max_val = max_val or 1

        n = len(pairs)
        slot_w = chart_w / n
        bar_w = max(6, min(48, slot_w * 0.6))

        value_fmt = value_fmt or (lambda v: str(int(v)))

        for i, (label, value) in enumerate(pairs):
            bar_h = (value / max_val) * chart_h if max_val else 0
            x_center = margin_left + slot_w * i + slot_w / 2
            x0 = x_center - bar_w / 2
            x1 = x_center + bar_w / 2
            y1 = height - margin_bottom
            y0 = y1 - bar_h

            draw.rectangle([x0, y0, x1, y1], fill=self._hex_to_rgb(color))

            val_text = value_fmt(value)
            tw = draw.textlength(val_text) if hasattr(draw, "textlength") else len(val_text) * 6
            draw.text((x_center - tw / 2, max(2, y0 - 16)), val_text,
                      fill=self._hex_to_rgb(self.TEXT_PRIMARY))

            label_text = str(label)
            if len(label_text) > 8 and n > 8:
                label_text = label_text[-5:]  # e.g. "24-03" from "2024-03" when crowded
            lw = draw.textlength(label_text) if hasattr(draw, "textlength") else len(label_text) * 6
            draw.text((x_center - lw / 2, height - margin_bottom + 6), label_text,
                      fill=self._hex_to_rgb(self.TEXT_SECONDARY))

        # Baseline
        draw.line([margin_left, height - margin_bottom, width - margin_right, height - margin_bottom],
                  fill=self._hex_to_rgb(self.TEXT_MUTED))
        return img

    @staticmethod
    def _hex_to_rgb(hex_color):
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    # -- Stat card breakdown popup (Total Plays / Minutes / Artists) --------
    def open_stats_breakdown(self, kind):
        """kind is one of: 'plays', 'minutes', 'artists'."""
        titles = {
            "plays":   "Total Plays Breakdown",
            "minutes": "Minutes Listened Breakdown",
            "artists": "Unique Artists Breakdown",
        }
        win = tk.Toplevel(self.root)
        win.title(titles.get(kind, "Breakdown"))
        win.geometry("620x480")
        win.configure(bg=self.CARD_BG)

        header = tk.Frame(win, bg=self.CARD_BG)
        header.pack(fill=tk.X, padx=20, pady=(20, 10))
        tk.Label(header, text=titles.get(kind, "Breakdown"), font=(self.FONT, 14, "bold"),
                 bg=self.CARD_BG, fg=self.ACCENT_GREEN).pack(side=tk.LEFT)

        toggle_frame = tk.Frame(header, bg=self.CARD_BG)
        toggle_frame.pack(side=tk.RIGHT)

        view_mode = {"value": "yearly"}
        chart_label = tk.Label(win, bg=self.CARD_BG)
        chart_label.pack(padx=20, pady=(0, 10))
        chart_photo_holder = {"photo": None}

        table_frame = tk.Frame(win, bg=self.CARD_BG)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        cols = ("period", "value")
        table = ttk.Treeview(table_frame, columns=cols, show="headings",
                             style="Dark.Treeview", selectmode="browse")
        col_labels = {"plays": "Plays", "minutes": "Minutes", "artists": "Artists"}
        table.heading("period", text="Period", anchor=tk.W)
        table.heading("value", text=col_labels.get(kind, "Value"), anchor=tk.CENTER)
        table.column("period", width=140, anchor=tk.W)
        table.column("value", width=100, anchor=tk.CENTER)
        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=table.yview)
        table.configure(yscrollcommand=vsb.set)
        table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        table.tag_configure("odd", background=self.CARD_BG)
        table.tag_configure("even", background=self.ROW_ALT)

        def value_index_for_kind():
            # get_yearly_stats() / get_monthly_stats() rows are:
            # (period, play_count, total_seconds, unique_artists)
            return {"plays": 1, "minutes": 2, "artists": 3}.get(kind, 1)

        def extract_value(row):
            idx = value_index_for_kind()
            raw = row[idx]
            if kind == "minutes":
                return int(raw or 0) // 60
            return int(raw or 0)

        def refresh():
            try:
                if view_mode["value"] == "yearly":
                    rows = self._db.get_yearly_stats()
                else:
                    rows = self._db.get_monthly_stats(months_back=24)
            except Exception as exc:
                print(f"[UI] Breakdown load error: {exc}")
                rows = []

            pairs = [(row[0], extract_value(row)) for row in rows]

            fmt = (lambda v: f"{v:,}") if kind != "minutes" else (lambda v: f"{v:,}m")
            chart_img = self._render_bar_chart(pairs, width=560, height=240, value_fmt=fmt)
            photo = ImageTk.PhotoImage(chart_img)
            chart_photo_holder["photo"] = photo  # keep alive
            chart_label.configure(image=photo)

            table.delete(*table.get_children())
            for i, (period, value) in enumerate(reversed(pairs)):  # most recent first
                tag = "even" if i % 2 else "odd"
                display_val = f"{value:,}" if kind != "minutes" else f"{value:,} min"
                table.insert("", tk.END, values=(period, display_val), tags=(tag,))

            if not pairs:
                self.import_status_label if False else None  # no-op, keeps linters quiet

        def set_mode(mode):
            view_mode["value"] = mode
            yearly_btn.config(bg=self.ACCENT_GREEN if mode == "yearly" else self.CARD_BG_2)
            monthly_btn.config(bg=self.ACCENT_GREEN if mode == "monthly" else self.CARD_BG_2)
            refresh()

        yearly_btn = tk.Button(toggle_frame, text="By Year", font=(self.FONT, 9, "bold"),
                              bg=self.ACCENT_GREEN, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                              padx=10, pady=4, command=lambda: set_mode("yearly"))
        yearly_btn.pack(side=tk.LEFT, padx=(0, 6))
        monthly_btn = tk.Button(toggle_frame, text="By Month (24mo)", font=(self.FONT, 9, "bold"),
                               bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                               padx=10, pady=4, command=lambda: set_mode("monthly"))
        monthly_btn.pack(side=tk.LEFT)

        refresh()

        tk.Button(win, text="Close", font=(self.FONT, 9), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, padx=12, pady=5, command=win.destroy).pack(pady=(0, 16))

    # -- Song detail popup ----------------------------------------------------
    def open_song_detail(self, title, artist):
        if not title or not artist:
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("520x560")
        win.configure(bg=self.CARD_BG)

        header = tk.Frame(win, bg=self.CARD_BG)
        header.pack(fill=tk.X, padx=20, pady=20)

        art_label = tk.Label(header, bg=self.CARD_BG)
        art_label.pack(side=tk.LEFT, padx=(0, 16))
        placeholder = ImageTk.PhotoImage(self._make_note_icon(96))
        art_label.image = placeholder
        art_label.configure(image=placeholder)

        info = tk.Frame(header, bg=self.CARD_BG)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(info, text=title, font=(self.FONT, 15, "bold"),
                 bg=self.CARD_BG, fg=self.TEXT_PRIMARY, wraplength=360, justify=tk.LEFT
                 ).pack(anchor=tk.W)

        artist_link = tk.Label(info, text=artist, font=(self.FONT, 11),
                               bg=self.CARD_BG, fg=self.ACCENT_BLUE, cursor="hand2")
        artist_link.pack(anchor=tk.W, pady=(2, 6))
        artist_link.bind("<Button-1>", lambda e: (win.destroy(), self.open_artist_detail(artist)))

        stats_label = tk.Label(info, text="Loading\u2026", font=(self.FONT, 9),
                               bg=self.CARD_BG, fg=self.TEXT_SECONDARY, justify=tk.LEFT)
        stats_label.pack(anchor=tk.W)

        hist_frame = tk.Frame(win, bg=self.CARD_BG)
        hist_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        tk.Label(hist_frame, text="Play History", font=(self.FONT, 10, "bold"),
                 bg=self.CARD_BG, fg=self.ACCENT_GREEN).pack(anchor=tk.W, pady=(0, 6))

        cols = ("when", "duration", "source")
        tree = ttk.Treeview(hist_frame, columns=cols, show="headings",
                            style="Dark.Treeview", selectmode="browse")
        tree.heading("when", text="Played At", anchor=tk.W)
        tree.heading("duration", text="Time", anchor=tk.CENTER)
        tree.heading("source", text="Source", anchor=tk.CENTER)
        tree.column("when", width=200, anchor=tk.W)
        tree.column("duration", width=90, anchor=tk.CENTER)
        tree.column("source", width=90, anchor=tk.CENTER)
        vsb = ttk.Scrollbar(hist_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("odd", background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)

        try:
            song_row = self._db.get_song_stats((title, artist))
            history = self._db.get_song_history(title, artist, limit=100)
        except Exception as exc:
            print(f"[UI] Song detail load error: {exc}")
            song_row = None
            history = []

        if song_row:
            plays = song_row[2]
            total_seconds = song_row[3] or 0
            last_played = song_row[4]
            art_url = self._row_field(tuple(song_row), 5)
        else:
            plays = len(history)
            total_seconds = sum(h[1] or 0 for h in history)
            last_played = history[0][0] if history else None
            art_url = None

        stats_label.config(
            text=f"{plays:,} plays  \u2022  {self._fmt_dur(total_seconds)} total\n"
                 f"Last played: {last_played or '\u2013'}"
        )

        for i, (played_at, duration, source) in enumerate(history):
            tag = "even" if i % 2 else "odd"
            tree.insert("", tk.END, values=(played_at or "\u2013", self._fmt_dur(duration), source),
                       tags=(tag,))

        if not history:
            tk.Label(hist_frame, text="No individual play records yet.",
                     font=(self.FONT, 9), bg=self.CARD_BG, fg=self.TEXT_MUTED
                     ).pack(anchor=tk.W, pady=8)

        if art_url:
            def apply(data):
                img = self._bytes_to_round_photo(data, 96)
                if img:
                    try:
                        if art_label.winfo_exists():
                            art_label.image = img
                            art_label.configure(image=img)
                    except Exception:
                        pass
            self._fetch_icon_async(art_url, apply)

        tk.Button(win, text="Close", font=(self.FONT, 9), bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                  relief=tk.FLAT, padx=12, pady=5, command=win.destroy).pack(pady=(0, 16))

    # -- Album art (Now Playing card) ---------------------------------------
    ART_SIZE = 96

    @classmethod
    def _rounded(cls, img, radius=16):
        img  = img.convert("RGBA")
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, img.size[0]-1, img.size[1]-1], radius=radius, fill=255)
        img.putalpha(mask)
        return img

    @classmethod
    def _placeholder_art(cls, size=None):
        size = size or cls.ART_SIZE
        img  = Image.new("RGBA", (size, size), (29, 185, 84, 255))
        d    = ImageDraw.Draw(img)
        w    = "white"
        d.rectangle([size//2-2, size//4, size//2+2, size//2+size//4], fill=w)
        cy = size//2 + size//6
        d.ellipse([size//2-10, cy-7,  size//2,    cy+3],  fill=w)
        d.ellipse([size//2+4,  cy+1,  size//2+14, cy+11], fill=w)
        return cls._rounded(img, radius=size//6)

    def _set_art(self, thumbnail_bytes):
        if thumbnail_bytes == self._art_cache_key:
            return
        self._art_cache_key = thumbnail_bytes
        try:
            if thumbnail_bytes:
                img  = Image.open(io.BytesIO(thumbnail_bytes)).convert("RGB")
                side = min(img.size)
                l    = (img.size[0]-side)//2
                t    = (img.size[1]-side)//2
                img  = img.crop((l, t, l+side, t+side))
                img  = img.resize((self.ART_SIZE, self.ART_SIZE), Image.LANCZOS)
                img  = self._rounded(img, radius=self.ART_SIZE//6)
            else:
                img = self._placeholder_art()
        except Exception:
            img = self._placeholder_art()
        self._art_photo = ImageTk.PhotoImage(img)
        self.art_label.configure(image=self._art_photo)

    # -- Helpers ------------------------------------------------------------
    def _emit(self, action, data=None):
        if self.tracker_callback:
            self.tracker_callback(action, data)

    @staticmethod
    def _fmt_dur(seconds):
        s = int(seconds or 0)
        if s < 60:    return f"{s}s"
        if s < 3600:  return f"{s//60}m {s%60:02d}s"
        return f"{s//3600}h {(s%3600)//60}m"

    @staticmethod
    def _fmt_clock(seconds):
        s = max(0, int(seconds or 0))
        return f"{s//60}:{s%60:02d}"

    def format_duration(self, seconds):
        return self._fmt_dur(seconds)

    # -- Database editor ------------------------------------------------------
    def open_database_editor(self):
        if self.db_window and self.db_window.winfo_exists():
            self.db_window.lift()
            return

        self._db_editor_db = self._db
        win = tk.Toplevel(self.root)
        win.title("Edit Database")
        win.geometry("700x440")
        win.configure(bg=self.CARD_BG)
        self.db_window = win

        left  = tk.Frame(win, bg=self.CARD_BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        right = tk.Frame(win, bg=self.CARD_BG, width=270)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)

        tk.Label(left, text="Songs (top 300 by play count)", bg=self.CARD_BG,
                 fg=self.TEXT_PRIMARY, font=(self.FONT, 10, "bold")).pack(anchor=tk.W, pady=(0, 4))

        self.db_tree = ttk.Treeview(left, columns=("title","artist","plays"),
                                    show="headings", style="Dark.Treeview", selectmode="browse")
        for col, lbl, w in (("title","Title",220),("artist","Artist",160),("plays","Plays",60)):
            self.db_tree.heading(col, text=lbl)
            self.db_tree.column(col, width=w, anchor=tk.W if col!="plays" else tk.CENTER)
        self.db_tree.pack(fill=tk.BOTH, expand=True)
        self.db_tree.bind("<<TreeviewSelect>>", lambda _e: self._db_on_select())

        tk.Label(right, text="Song Details", bg=self.CARD_BG, fg=self.TEXT_PRIMARY,
                 font=(self.FONT, 10, "bold")).pack(anchor=tk.W, pady=(0, 8))

        form = tk.Frame(right, bg=self.CARD_BG)
        form.pack(fill=tk.X)
        entries = {}
        for row, (key, lbl) in enumerate((("title","Title:"),("artist","Artist:"),
                                          ("plays","Plays:"),("duration","Duration (s):"))):
            tk.Label(form, text=lbl, bg=self.CARD_BG, fg=self.TEXT_PRIMARY,
                     font=(self.FONT, 9)).grid(row=row, column=0, sticky=tk.W, pady=4, padx=(0,8))
            e = tk.Entry(form, width=24, bg=self.CARD_BG_2, fg=self.TEXT_PRIMARY,
                         insertbackground=self.TEXT_PRIMARY, relief=tk.FLAT)
            e.grid(row=row, column=1, pady=4)
            entries[key] = e

        self._db_entries = entries
        self._db_orig_key = None

        btns = tk.Frame(right, bg=self.CARD_BG)
        btns.pack(fill=tk.X, pady=(12, 0))
        tk.Button(btns, text="Save", command=self._db_save,
                  bg=self.ACCENT_GREEN, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                  padx=12, pady=5).pack(side=tk.LEFT, padx=(0,6))
        tk.Button(btns, text="Delete", command=self._db_delete,
                  bg=self.ACCENT_RED, fg=self.TEXT_PRIMARY, relief=tk.FLAT,
                  padx=12, pady=5).pack(side=tk.LEFT)

        self._db_refresh()

    def _db_refresh(self):
        try:
            songs = list(self._db_editor_db.get_all_songs(limit=300))
        except Exception:
            songs = []
        self.db_tree.delete(*self.db_tree.get_children())
        for row in songs:
            title, artist, plays = row[0], row[1], row[2]
            self.db_tree.insert("", tk.END, values=(title, artist, plays))

    def _db_on_select(self):
        sel = self.db_tree.selection()
        if not sel:
            return
        title, artist, plays = self.db_tree.item(sel[0], "values")[:3]
        stats = self._db_editor_db.get_song_stats((title, artist))
        dur   = stats[3] if stats else 0
        self._db_orig_key = (title, artist)
        for key, val in (("title",title),("artist",artist),("plays",plays),("duration",dur)):
            e = self._db_entries[key]
            e.delete(0, tk.END)
            e.insert(0, str(val))

    def _db_save(self):
        title  = self._db_entries["title"].get().strip()
        artist = self._db_entries["artist"].get().strip()
        try:    plays = int(self._db_entries["plays"].get().strip() or 0)
        except: plays = 0
        try:    dur   = int(self._db_entries["duration"].get().strip() or 0)
        except: dur   = 0
        if not title or not artist:
            return
        conn = self._db_editor_db.get_connection()
        cur  = conn.cursor()
        try:
            if self._db_orig_key and self._db_orig_key != (title, artist):
                cur.execute("DELETE FROM songs WHERE title=? AND artist=?", self._db_orig_key)
            cur.execute("SELECT COUNT(*) FROM songs WHERE title=? AND artist=?", (title, artist))
            if cur.fetchone()[0]:
                cur.execute("UPDATE songs SET play_count=?, total_duration=?, "
                            "last_played=CURRENT_TIMESTAMP WHERE title=? AND artist=?",
                            (plays, dur, title, artist))
            else:
                cur.execute("INSERT INTO songs (title,artist,play_count,total_duration,last_played) "
                            "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                            (title, artist, max(1, plays), dur))
            conn.commit()
        except Exception as exc:
            print(f"[UI] DB save error: {exc}")
            conn.rollback()
        finally:
            conn.close()
        self._db_refresh()

    def _db_delete(self):
        title  = self._db_entries["title"].get().strip()
        artist = self._db_entries["artist"].get().strip()
        if not title:
            return
        conn = self._db_editor_db.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute("DELETE FROM songs WHERE title=? AND artist=?", (title, artist))
            cur.execute("DELETE FROM listen_history WHERE title=? AND artist=?", (title, artist))
            conn.commit()
        except Exception as exc:
            print(f"[UI] DB delete error: {exc}")
            conn.rollback()
        finally:
            conn.close()
        self._db_refresh()
