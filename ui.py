import io
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


class SpotifyStatsUI:
    # ── Palette ──────────────────────────────────────────────────────────
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

    def __init__(self, root, tracker_callback=None):
        self.root = root
        self.tracker_callback = tracker_callback
        self.settings_menu = None
        self.db_window     = None
        self._db           = None

        # Visibility toggles
        self.show_controls    = tk.BooleanVar(value=True)
        self.show_totals      = tk.BooleanVar(value=True)
        self.show_top_songs   = tk.BooleanVar(value=True)
        self.show_top_artists = tk.BooleanVar(value=True)
        self.always_on_top    = tk.BooleanVar(value=False)

        # Crossfade compensation (0-12s); only active when source is window_title
        self.crossfade_var    = tk.IntVar(value=0)
        self._last_source     = None        # last known playback source
        self._crossfade_slider = None       # reference to slider widget when open

        # Art cache – keeps PhotoImage alive so Tk doesn't GC it
        self._art_photo     = None
        self._art_cache_key = None  # bytes or None

        # Table state
        self._songs_data    = []
        self._artists_data  = []
        self._songs_sort    = ("plays", True)
        self._artists_sort  = ("plays", True)
        self._songs_filter  = tk.StringVar()
        self._artists_filter = tk.StringVar()
        self._songs_filter.trace_add("write",   lambda *_: self._render_songs_table())
        self._artists_filter.trace_add("write",  lambda *_: self._render_artists_table())

        self.root.title("Spotify Stats Tracker")
        self.root.geometry("1020x800")
        self.root.minsize(760, 560)
        self.root.configure(bg=self.DARK_BG)
        self._set_window_icon()
        self._setup_styles()
        self._build_ui()

    # ── Icon ─────────────────────────────────────────────────────────────
    def _set_window_icon(self):
        try:
            self._icon_photo = ImageTk.PhotoImage(self._placeholder_art(48))
            self.root.iconphoto(False, self._icon_photo)
        except Exception:
            pass

    # ── ttk styles ───────────────────────────────────────────────────────
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
                    foreground=self.TEXT_PRIMARY, borderwidth=0, rowheight=26,
                    font=(self.FONT, 9))
        s.map("Dark.Treeview",
              background=[("selected", "#2A2A2A")],
              foreground=[("selected", self.TEXT_PRIMARY)])
        s.configure("Dark.Treeview.Heading",
                    background=self.CARD_BG_2, foreground=self.TEXT_SECONDARY,
                    borderwidth=0, font=(self.FONT, 9, "bold"), relief=tk.FLAT)
        s.map("Dark.Treeview.Heading", background=[("active", self.CARD_BG_2)])
        s.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])

    # ── Top-level layout ─────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header(self.root)

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        self._build_now_playing(self.main_frame)
        self._build_totals(self.main_frame)

        self.stats_container = ttk.Frame(self.main_frame)
        self.stats_container.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        self.top_songs_frame   = self._build_table_section(self.stats_container, "TOP SONGS",   "songs")
        self.top_artists_frame = self._build_table_section(self.stats_container, "TOP ARTISTS", "artists")
        self._apply_visibility()

    # ── Header ───────────────────────────────────────────────────────────
    def _build_header(self, parent):
        header = tk.Frame(parent, bg=self.CARD_BG, height=68)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=18, pady=12)

        tk.Label(inner, text="♪  Spotify Stats",
                 font=(self.FONT, 20, "bold"), bg=self.CARD_BG, fg=self.ACCENT_GREEN
                 ).pack(side=tk.LEFT)

        btns = tk.Frame(inner, bg=self.CARD_BG)
        btns.pack(side=tk.RIGHT)

        self.settings_button = tk.Button(
            btns, text="⚙", font=("Segoe UI Symbol", 13),
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

    # ── Now-playing card ─────────────────────────────────────────────────
    def _build_now_playing(self, parent):
        self.current_frame = tk.Frame(parent, bg=self.CARD_BG)
        self.current_frame.pack(fill=tk.X)

        inner = tk.Frame(self.current_frame, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=20, pady=20)

        # Album art
        self.art_label = tk.Label(inner, bg=self.CARD_BG, bd=0)
        self.art_label.pack(side=tk.LEFT, padx=(0, 18), anchor=tk.N)
        self._set_art(None)

        # Info column
        info = tk.Frame(inner, bg=self.CARD_BG)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # "NOW PLAYING" + source badge
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

        self.artist_label = tk.Label(info, text="–", font=(self.FONT, 13),
                                     bg=self.CARD_BG, fg=self.TEXT_SECONDARY, anchor=tk.W)
        self.artist_label.pack(anchor=tk.W, fill=tk.X)

        # Progress bar row
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

        # Transport controls + status
        bottom_row = tk.Frame(info, bg=self.CARD_BG)
        bottom_row.pack(fill=tk.X, pady=(14, 0))

        self.controls_frame = tk.Frame(bottom_row, bg=self.CARD_BG)
        self.controls_frame.pack(side=tk.LEFT)

        self.btn_prev    = self._ctrl_btn(self.controls_frame, "⏮", "previous")
        self.btn_play    = self._ctrl_btn(self.controls_frame, "▶", "play_pause", wide=True)
        self.btn_next    = self._ctrl_btn(self.controls_frame, "⏭", "next")
        self.btn_shuffle = self._ctrl_btn(self.controls_frame, "🔀", "toggle_shuffle")
        self.btn_repeat  = self._ctrl_btn(self.controls_frame, "🔁", "cycle_repeat")

        status_col = tk.Frame(bottom_row, bg=self.CARD_BG)
        status_col.pack(side=tk.RIGHT, anchor=tk.E)
        self.status_label = tk.Label(status_col, text="", font=(self.FONT, 9, "bold"),
                                     bg=self.CARD_BG, fg=self.ACCENT_GREEN, anchor=tk.E)
        self.status_label.pack(anchor=tk.E)
        self.reconnect_button = tk.Button(
            status_col, text="Reconnect Spotify", font=(self.FONT, 8, "bold"),
            bg=self.ACCENT_BLUE, fg=self.TEXT_PRIMARY, relief=tk.FLAT, padx=8, pady=3,
            command=lambda: self._emit("reconnect"))
        # packed/unpacked dynamically in update_media_controls

        self.playcount_label = tk.Label(info, text="Plays: – | Time: –",
                                        font=(self.FONT, 9), bg=self.CARD_BG,
                                        fg=self.TEXT_SECONDARY, anchor=tk.W)
        self.playcount_label.pack(anchor=tk.W, pady=(10, 0), fill=tk.X)

    def _ctrl_btn(self, parent, label, action, wide=False):
        b = tk.Button(parent, text=label, font=(self.FONT, 11),
                      bg=self.DARK_BG, fg=self.TEXT_PRIMARY,
                      activebackground=self.ACCENT_GREEN, activeforeground=self.TEXT_PRIMARY,
                      relief=tk.FLAT, padx=(14 if wide else 10), pady=6,
                      command=lambda: self._emit(action))
        b.pack(side=tk.LEFT, padx=(0, 6))
        return b

    # ── Totals strip ─────────────────────────────────────────────────────
    def _build_totals(self, parent):
        self.totals_frame = tk.Frame(parent, bg=self.CARD_BG)
        self.totals_frame.pack(fill=tk.X, pady=(14, 0))

        inner = tk.Frame(self.totals_frame, bg=self.CARD_BG)
        inner.pack(fill=tk.X, padx=10, pady=14)

        self.lbl_plays,   _ = self._stat_card(inner, "TOTAL PLAYS")
        self.lbl_minutes, _ = self._stat_card(inner, "MINUTES LISTENED")
        self.lbl_artists, _ = self._stat_card(inner, "UNIQUE ARTISTS")

    def _stat_card(self, parent, label_text):
        card = tk.Frame(parent, bg=self.CARD_BG)
        card.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10)
        val = tk.Label(card, text="0", font=(self.FONT, 22, "bold"),
                       bg=self.CARD_BG, fg=self.TEXT_PRIMARY)
        val.pack(anchor=tk.W)
        lbl = tk.Label(card, text=label_text, font=(self.FONT, 8, "bold"),
                       bg=self.CARD_BG, fg=self.TEXT_SECONDARY)
        lbl.pack(anchor=tk.W)
        return val, lbl

    # ── Sortable/searchable table section ────────────────────────────────
    def _build_table_section(self, parent, title_text, kind):
        frame = tk.Frame(parent, bg=self.CARD_BG)

        hdr = tk.Frame(frame, bg=self.CARD_BG)
        hdr.pack(fill=tk.X, padx=15, pady=(15, 8))
        tk.Label(hdr, text=title_text, font=(self.FONT, 11, "bold"),
                 bg=self.CARD_BG, fg=self.ACCENT_GREEN).pack(side=tk.LEFT)

        fvar = self._songs_filter if kind == "songs" else self._artists_filter
        tk.Label(hdr, text="🔎", bg=self.CARD_BG, fg=self.TEXT_MUTED).pack(side=tk.RIGHT)
        tk.Entry(hdr, textvariable=fvar, font=(self.FONT, 9), bg=self.CARD_BG_2,
                 fg=self.TEXT_PRIMARY, insertbackground=self.TEXT_PRIMARY,
                 relief=tk.FLAT, width=16).pack(side=tk.RIGHT, padx=(0, 4))

        list_frame = tk.Frame(frame, bg=self.CARD_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))

        if kind == "songs":
            cols     = ("title", "artist", "plays", "time")
            headings = {"title": "Title", "artist": "Artist", "plays": "Plays", "time": "Time"}
        else:
            cols     = ("name", "plays", "time")
            headings = {"name": "Artist", "plays": "Plays", "time": "Time"}

        tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                            style="Dark.Treeview", selectmode="browse")
        for col in cols:
            anchor = tk.W if col in ("title", "artist", "name") else tk.CENTER
            width  = 210 if col in ("title", "artist", "name") else 70
            tree.heading(col, text=headings[col], anchor=anchor,
                         command=lambda c=col, k=kind: self._on_sort(k, c))
            tree.column(col, width=width, anchor=anchor,
                        stretch=(col in ("title", "artist", "name")))

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        tree.tag_configure("odd",  background=self.CARD_BG)
        tree.tag_configure("even", background=self.ROW_ALT)

        if kind == "songs":
            self.songs_tree = tree
        else:
            self.artists_tree = tree
        return frame

    # ── Settings panel ───────────────────────────────────────────────────
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
        self._toggle_row(panel, "Playback Controls",  self.show_controls)
        self._toggle_row(panel, "Totals Strip",        self.show_totals)
        self._toggle_row(panel, "Top Songs",           self.show_top_songs)
        self._toggle_row(panel, "Top Artists",         self.show_top_artists)

        _sect("Window")
        self._toggle_row(panel, "Always on Top", self.always_on_top,
                         cmd=lambda: self.root.attributes("-topmost", self.always_on_top.get()))

        _sect("Crossfade Compensation")

        # Explanation label
        exact_source = self._last_source in ("local", "remote")
        cf_note_text = (
            "Disabled — exact timing available via Spotify account."
            if exact_source else
            "Match this to Spotify's crossfade setting.\n"
            "Added to each song's time when the title flips early."
        )
        cf_note = tk.Label(panel, text=cf_note_text, font=(self.FONT, 8),
                           bg=self.CARD_BG, fg=self.TEXT_MUTED,
                           justify=tk.LEFT, wraplength=270)
        cf_note.pack(anchor=tk.W, pady=(0, 4))

        # Slider row
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
            cf_slider.config(state=tk.DISABLED, fg=self.TEXT_MUTED,
                             troughcolor=self.CARD_BG)

        _sect("Data")
        tk.Button(panel, text="Edit Database…",
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
        # Controls
        if self.show_controls.get():
            if not self.controls_frame.winfo_manager():
                self.controls_frame.pack(side=tk.LEFT)
        else:
            self.controls_frame.pack_forget()

        # Totals
        if self.show_totals.get():
            self.totals_frame.pack(fill=tk.X, pady=(14, 0))
        else:
            self.totals_frame.pack_forget()

        # Tables
        show_s = self.show_top_songs.get()
        show_a = self.show_top_artists.get()
        if show_s or show_a:
            if not self.stats_container.winfo_manager():
                self.stats_container.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        else:
            self.stats_container.pack_forget()

        self.top_songs_frame.pack_forget()
        self.top_artists_frame.pack_forget()
        if show_s and show_a:
            self.top_songs_frame.pack(side=tk.LEFT,  fill=tk.BOTH, expand=True, padx=(0, 7))
            self.top_artists_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(7, 0))
        elif show_s:
            self.top_songs_frame.pack(fill=tk.BOTH, expand=True)
        elif show_a:
            self.top_artists_frame.pack(fill=tk.BOTH, expand=True)

    # Public alias expected by main.py
    def apply_visibility_settings(self):
        self._apply_visibility()

    # ── Data update methods (called from main.py on Tk thread) ──────────
    def update_current_track(self, song, artist, plays, duration,
                             playback=None, playback_state="idle"):
        playback = playback or {}
        self._set_art(playback.get("thumbnail"))

        # Track the source so the settings panel knows whether to enable the
        # crossfade slider when it's next opened.
        new_source = playback.get("source")
        if new_source and new_source != self._last_source:
            self._last_source = new_source
            self._refresh_crossfade_slider_state()

        if playback_state == "idle":
            self.song_label.config(text="Not playing", fg=self.TEXT_PRIMARY)
            self.artist_label.config(text="–", fg=self.TEXT_SECONDARY)
            self.playcount_label.config(text="Plays: – | Time: –")
            self.status_label.config(text="", fg=self.TEXT_SECONDARY)
        else:
            dim = playback_state in ("paused_closed", "disconnected")
            fg  = self.TEXT_MUTED if dim else self.TEXT_PRIMARY
            self.song_label.config(text=song or "Unknown", fg=fg)
            self.artist_label.config(text=artist or "–", fg=self.TEXT_MUTED if dim else self.TEXT_SECONDARY)
            self.playcount_label.config(text=f"Plays: {plays} | Time: {self._fmt_dur(duration)}")

            if playback_state == "disconnected":
                self.status_label.config(text="Disconnected", fg=self.ACCENT_RED)
            elif playback_state == "paused_closed":
                self.status_label.config(text="Paused", fg=self.TEXT_MUTED)
            else:
                self.status_label.config(text="Playing", fg=self.ACCENT_GREEN)

        src_txt = {"local": "💻 DESKTOP", "remote": "📱 REMOTE"}.get(playback.get("source"), "")
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
                             text="⏸" if is_playing else "▶")
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
            text="🔂" if repeat_mode == "track" else "🔁")

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

    # ── Table rendering ──────────────────────────────────────────────────
    def _on_sort(self, kind, col):
        current = self._songs_sort if kind == "songs" else self._artists_sort
        reverse = not current[1] if current[0] == col else (col in ("plays", "time"))
        if kind == "songs":
            self._songs_sort = (col, reverse)
            self._render_songs_table()
        else:
            self._artists_sort = (col, reverse)
            self._render_artists_table()

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
        for i, (title, artist, plays, dur, _) in enumerate(rows):
            tree.insert("", tk.END,
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
        for i, (name, plays, dur, _) in enumerate(rows):
            tree.insert("", tk.END,
                        values=(name, plays, self._fmt_dur(dur)),
                        tags=("even" if i % 2 else "odd",))

    # ── Album art ────────────────────────────────────────────────────────
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

    # ── Helpers ──────────────────────────────────────────────────────────
    def _emit(self, action, data=None):
        if self.tracker_callback:
            self.tracker_callback(action, data)

    def _refresh_crossfade_slider_state(self):
        """Enable or disable the crossfade slider (if open) based on the
        current source. Called whenever the source changes mid-session."""
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

    # Public alias used by format strings in main.py
    def format_duration(self, seconds):
        return self._fmt_dur(seconds)

    # ── Database editor ──────────────────────────────────────────────────
    def open_database_editor(self):
        from database import SpotifyDatabase
        if self.db_window and self.db_window.winfo_exists():
            self.db_window.lift()
            return

        self._db = SpotifyDatabase()
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
            songs = list(self._db.get_all_songs(limit=300))
        except Exception:
            songs = []
        self.db_tree.delete(*self.db_tree.get_children())
        for title, artist, plays, _dur, _last in songs:
            self.db_tree.insert("", tk.END, values=(title, artist, plays))

    def _db_on_select(self):
        sel = self.db_tree.selection()
        if not sel:
            return
        title, artist, plays = self.db_tree.item(sel[0], "values")[:3]
        stats = self._db.get_song_stats((title, artist))
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
        conn = self._db.get_connection()
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
        conn = self._db.get_connection()
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