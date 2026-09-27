"""Discord-themed GUI for the Bulk Delete Tool (CustomTkinter).

Layout & visual language inspired by Discord and the ClearVision theme
(https://github.com/ClearVision/ClearVision-v6, Apache-2.0):
server rail → channel sidebar → dense chat-style message stream over an
aurora wallpaper generated procedurally at runtime (theme.py).

Browse without IDs: after login the rail lists your servers and the sidebar
lists your DMs / text channels — click one and your messages appear.
Selection: click, drag-sweep, or Ctrl+A. DELETE arms, ENTER confirms, ESC
cancels.
"""

import contextlib
import ctypes
import queue
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

import settings as settings_store
import theme
from api_client import AuthenticationError, DiscordAPIError, DiscordClient, NetworkError
from deleter import MessageDeleter
from token_finder import find_tokens
from utils import (
    TIME_WINDOWS,
    VERSION,
    date_to_snowflake,
    display_username,
    format_timestamp_compact,
    parse_date,
    print_info,
    relative_snowflake,
)

# Crisp rendering on high-DPI Windows displays (must happen before Tk init)
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()

WALLPAPER_SIZE = (2400, 1500)
ROW_BATCH_SIZE = 60
ROW_FLUSH_DELAY_MS = 10
GROUP_WINDOW_SECONDS = 420  # Discord groups consecutive messages within ~7 minutes
AVATAR_CACHE = {}
CDN_IMAGE_CACHE = {}

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

C = theme.COLORS
FONT = "Segoe UI"
TIME_RANGE_VALUES = ["Any time", *TIME_WINDOWS.keys(), "Custom dates…"]


def avatar_ctk_image(name, size_px):
    """Cached CTkImage avatar for a username (or the logged-out default)."""
    key = (name, size_px)
    if key not in AVATAR_CACHE:
        if name:
            pil = theme.build_avatar(name[0], theme.avatar_color_for_name(name), 96)
        else:
            pil = theme.default_avatar(96)
        AVATAR_CACHE[key] = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size_px, size_px))
    return AVATAR_CACHE[key]


def cdn_ctk_image(url, fallback_name, size_px):
    """Circular CDN image with initials-avatar fallback, cached per key."""
    key = ("cdn", url, size_px)
    if key in CDN_IMAGE_CACHE:
        return CDN_IMAGE_CACHE[key]
    pil = theme.fetch_discord_image(url, size_px)
    if pil is None:
        return avatar_ctk_image(fallback_name, size_px)
    image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size_px, size_px))
    CDN_IMAGE_CACHE[key] = image
    return image


class DiscordToolGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Discord Bulk Delete Tool")
        self.geometry("1280x840")
        self.minsize(1080, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(fg_color=C["rail"])

        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State
        self.client = None
        self.deleter = None
        self.token = ""
        self.logged_in_user = None
        self.app_settings = settings_store.load_settings()
        self.scanned_messages = []
        self.selected_ids = set()
        self.check_vars = {}
        self.rows = {}
        self.session_deleted = 0
        self.is_scanning = False
        self.is_deleting = False
        self.armed = False
        self.stop_event = threading.Event()
        self._pending_rows = []
        self._row_flush_scheduled = False
        self._last_rendered_msg = None
        self._sweeping = False
        self._sweep_pending = False
        self._ui_queue = queue.Queue()
        self._scan_generation = 0  # bumped on every clear; stale renders dropped

        self.guilds = []
        self.dm_channels = []
        self.guild_channels = {}       # guild_id -> [channels]
        self.current_context = None    # {"kind": "home"|"guild", "channel": {...}}

        self._build_wallpaper_source()
        self._build_rail()
        self._build_sidebar()
        self._build_main()
        self._bind_keyboard()
        self._show_home()
        self._set_status("Not logged in — paste your token to begin.")
        self._apply_window_chrome()
        self._poll_ui_queue()

    # --- Thread-safe UI bridge ----------------------------------------------------
    #
    # Worker threads never call tkinter directly: they push callables with
    # _post(), and the main thread drains the queue on a 30ms timer. This is
    # race-free under both mainloop() and manual update() pumping.

    def _post(self, fn):
        self._ui_queue.put(fn)

    def _poll_ui_queue(self):
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                with contextlib.suppress(Exception):
                    fn()
        except queue.Empty:
            pass
        finally:
            self.after(30, self._poll_ui_queue)

    # --- Window chrome ---------------------------------------------------------

    def _apply_window_chrome(self):
        """Custom icon + dark Windows title bar (kills the stock-tkinter tell)."""
        try:
            icon_path = Path(tempfile.gettempdir()) / "discord_bulk_delete_tool.ico"
            theme.save_app_icon(str(icon_path))
            self.iconbitmap(str(icon_path))
        except Exception:
            pass
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
                    value = ctypes.c_int(1)
                    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                    )
                    if result == 0:
                        break

    # --- Wallpaper -----------------------------------------------------------

    def _build_wallpaper_source(self):
        custom = theme.load_custom_background()
        if custom is not None:
            try:
                wallpaper = theme.prepare_background(custom, *WALLPAPER_SIZE)
            except OSError:
                wallpaper = theme.build_wallpaper(*WALLPAPER_SIZE)
        else:
            wallpaper = theme.build_wallpaper(*WALLPAPER_SIZE)
        self._wallpaper_pil = wallpaper
        self._wallpaper_image = ctk.CTkImage(
            light_image=wallpaper, dark_image=wallpaper, size=WALLPAPER_SIZE
        )
        self.panel_tint = theme.sample_panel_color(wallpaper)

    # --- Icon rail: home + server list ------------------------------------------

    def _build_rail(self):
        rail = ctk.CTkFrame(self, width=72, corner_radius=0, fg_color=C["rail"])
        rail.grid(row=0, column=0, sticky="nsew")
        rail.grid_rowconfigure(2, weight=1)

        self.btn_home = ctk.CTkButton(
            rail, text="🧹", width=44, height=44, corner_radius=22,
            fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont(family="Segoe UI Emoji", size=19),
            command=self._show_home,
        )
        self.btn_home.grid(row=0, column=0, padx=14, pady=(14, 8))
        self.home_pill = self._make_pill(rail)
        self.home_pill.place(x=0, rely=0.5, anchor="w", in_=self.btn_home)
        self.home_pill.place_forget()

        separator = ctk.CTkFrame(rail, height=1, width=36, fg_color=C["text_faint"])
        separator.grid(row=1, column=0, padx=18, pady=6)

        self.rail_guilds = ctk.CTkScrollableFrame(
            rail, fg_color="transparent", width=72,
            scrollbar_button_color=C["hover"], scrollbar_button_hover_color=C["text_muted"],
        )
        self.rail_guilds.grid(row=2, column=0, sticky="nsew", pady=4)

        bottom = ctk.CTkFrame(rail, fg_color="transparent")
        bottom.grid(row=3, column=0, pady=(6, 14))
        for glyph, command in (
            ("⚙\uFE0E", self.show_settings),
            ("i", self.show_about),
            ("↗", lambda: webbrowser.open("https://github.com/prafulaa/DiscordBulkDeleteTool")),
        ):
            ctk.CTkButton(
                bottom, text=glyph, width=44, height=44, corner_radius=16,
                fg_color=C["card"], hover_color=C["hover"], text_color=C["text_muted"],
                font=ctk.CTkFont(family=FONT, size=15, weight="bold"), command=command,
            ).pack(pady=4)

        self._guild_widgets = {}   # guild_id -> {"pill": frame}
        self._channel_rows = {}    # channel_id -> button

    @staticmethod
    def _make_pill(parent):
        return ctk.CTkFrame(parent, width=5, height=30, corner_radius=3, fg_color="#ffffff")

    def _load_guild_icons(self):
        """Fetch guild icons from the CDN in the background and swap them in."""
        for guild in self.guilds:
            url = theme.guild_icon_url(guild["id"], guild.get("icon"))
            if not url:
                continue
            pil = theme.fetch_discord_image(url, 40)
            if pil is not None:
                image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(40, 40))
                self.after(
                    0,
                    lambda g=guild, image=image: self._guild_widgets.get(g["id"], {})
                    .get("btn")
                    and self._guild_widgets[g["id"]]["btn"].configure(image=image),
                )

    def _populate_guild_rail(self):
        for widget in self.rail_guilds.winfo_children():
            widget.destroy()
        self._guild_widgets.clear()

        for guild in self.guilds:
            holder = ctk.CTkFrame(self.rail_guilds, width=72, height=52, fg_color="transparent")
            holder.pack(fill="x")
            holder.pack_propagate(False)

            pill = self._make_pill(holder)
            pill.place(x=0, rely=0.5, anchor="w")
            pill.place_forget()

            fallback = avatar_ctk_image(guild["name"], 40)
            btn = ctk.CTkButton(
                holder, text="", image=fallback, width=44, height=44, corner_radius=22,
                fg_color=C["card"], hover_color=C["hover"],
                command=lambda g=guild: self._select_guild(g),
            )
            btn.place(relx=0.5, rely=0.5, anchor="center")
            self._guild_widgets[guild["id"]] = {"pill": pill, "btn": btn}

        threading.Thread(target=self._load_guild_icons, daemon=True).start()

    # --- Sidebar: channel list + filters ------------------------------------------

    def _section(self, parent, title):
        """Left-aligned micro-caps label with a blurple accent bar (Discord style)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(14, 5))
        ctk.CTkFrame(row, width=3, height=13, corner_radius=2, fg_color=C["primary"]).pack(side="left")
        ctk.CTkLabel(
            row, text=title, anchor="w", text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
        ).pack(side="left", padx=8)

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color=C["sidebar"])
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.pack_propagate(False)

        self.sidebar_header = ctk.CTkFrame(sidebar, height=48, corner_radius=0, fg_color=C["header"])
        self.sidebar_header.pack(side="top", fill="x")
        self.lbl_context = ctk.CTkLabel(
            self.sidebar_header, text="Bulk Delete Tool", anchor="w", text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        )
        self.lbl_context.pack(side="left", padx=14)
        ctk.CTkLabel(
            self.sidebar_header, text=f"v{VERSION}", text_color=C["text_faint"],
            font=ctk.CTkFont(family=FONT, size=11),
        ).pack(side="right", padx=12)

        # Bottom-first packing: user panel → progress → cluster → channel list
        panel = ctk.CTkFrame(sidebar, height=62, corner_radius=0, fg_color=C["header"])
        panel.pack(side="bottom", fill="x")
        self.sidebar_progress = ctk.CTkProgressBar(
            sidebar, height=4, progress_color=C["primary"], fg_color=C["input"]
        )
        self.sidebar_progress.set(0)

        cluster = ctk.CTkFrame(sidebar, fg_color="transparent")
        cluster.pack(side="bottom", fill="x")

        self._section(cluster, "AUTHENTICATION")
        self.entry_token = ctk.CTkEntry(
            cluster, placeholder_text="Paste your user token", show="•", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_token.pack(fill="x", padx=14, pady=(2, 6))
        self.entry_token.bind("<Return>", lambda _e: self.login())

        auth_row = ctk.CTkFrame(cluster, fg_color="transparent")
        auth_row.pack(fill="x", padx=14, pady=(0, 4))
        auth_row.grid_columnconfigure(0, weight=1)
        auth_row.grid_columnconfigure(1, weight=1)
        self.btn_login = ctk.CTkButton(
            auth_row, text="Login", height=32, fg_color=C["primary"],
            hover_color=C["primary_hover"], command=self.login,
        )
        self.btn_login.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.btn_auto_token = ctk.CTkButton(
            auth_row, text="Auto-Find", height=32, fg_color=C["card"],
            hover_color=C["hover"], text_color=C["text_muted"], command=self.auto_find_token,
        )
        self.btn_auto_token.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        stats = ctk.CTkFrame(cluster, fg_color=C["card"], corner_radius=10)
        stats.pack(fill="x", padx=14, pady=(4, 4))
        stats.grid_columnconfigure((0, 1, 2), weight=1)
        self.stat_found = self._stat_cell(stats, 0, "FOUND")
        self.stat_selected = self._stat_cell(stats, 1, "SELECTED")
        self.stat_deleted = self._stat_cell(stats, 2, "DELETED")

        self._section(cluster, "FILTERS")
        self.entry_filter = ctk.CTkEntry(
            cluster, placeholder_text="Keyword filter (optional)", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_filter.pack(fill="x", padx=14, pady=(2, 6))

        self.time_range_var = ctk.StringVar(value="Any time")
        self.menu_time_range = ctk.CTkOptionMenu(
            cluster, values=TIME_RANGE_VALUES, variable=self.time_range_var, height=34,
            fg_color=C["input"], button_color=C["hover"], button_hover_color=C["card"],
            text_color=C["text"], font=ctk.CTkFont(family=FONT, size=12),
            corner_radius=8, command=self._on_time_range_change,
        )
        self.menu_time_range.pack(fill="x", padx=14, pady=(0, 6))

        self.dates_block = ctk.CTkFrame(cluster, fg_color="transparent")
        dates_row = ctk.CTkFrame(self.dates_block, fg_color="transparent")
        dates_row.pack(fill="x", padx=14)
        dates_row.grid_columnconfigure(0, weight=1)
        dates_row.grid_columnconfigure(1, weight=1)
        self.entry_after = ctk.CTkEntry(
            dates_row, placeholder_text="After date", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_after.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.entry_before = ctk.CTkEntry(
            dates_row, placeholder_text="Before date", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_before.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self.btn_scan = ctk.CTkButton(
            cluster, text="SCAN MESSAGES", height=38, corner_radius=8,
            fg_color=C["primary"], hover_color=C["primary_hover"],
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"), command=self._scan_current,
        )
        self.btn_scan.pack(fill="x", padx=14, pady=(10, 12))

        body = ctk.CTkFrame(sidebar, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True)

        self.lbl_channels_hint = ctk.CTkLabel(
            body, text="", anchor="w", text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
        )
        self.lbl_channels_hint.pack(fill="x", padx=16, pady=(12, 4))

        self.channel_list = ctk.CTkScrollableFrame(
            body, fg_color="transparent",
            scrollbar_button_color=C["hover"], scrollbar_button_hover_color=C["text_muted"],
        )
        self.channel_list.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        # User panel
        self.panel_avatar = ctk.CTkLabel(panel, text="", image=avatar_ctk_image(None, 36))
        self.panel_avatar.pack(side="left", padx=(12, 8), pady=12)
        names = ctk.CTkFrame(panel, fg_color="transparent")
        names.pack(side="left", fill="y", pady=10)
        self.lbl_username = ctk.CTkLabel(
            names, text="Not logged in", anchor="w", text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
        )
        self.lbl_username.pack(anchor="w")
        self.lbl_userstatus = ctk.CTkLabel(
            names, text="Paste a token to begin", anchor="w",
            text_color=C["text_muted"], font=ctk.CTkFont(family=FONT, size=10),
        )
        self.lbl_userstatus.pack(anchor="w")
        self.btn_logout = ctk.CTkButton(
            panel, text="✕", width=28, height=28, corner_radius=14,
            fg_color="transparent", hover_color=C["hover"], text_color=C["text_faint"],
            font=ctk.CTkFont(family=FONT, size=12, weight="bold"), command=self.logout,
        )
        self.btn_logout.pack(side="right", padx=12)

    @staticmethod
    def _stat_cell(parent, column, label):
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=0, column=column, pady=10, sticky="ew")
        value = ctk.CTkLabel(
            cell, text="0", text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
        )
        value.pack()
        ctk.CTkLabel(
            cell, text=label, text_color=C["text_faint"],
            font=ctk.CTkFont(family=FONT, size=9, weight="bold"),
        ).pack()
        return value

    # --- Main area ----------------------------------------------------------------

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=self.panel_tint)
        main.grid(row=0, column=2, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=0)
        main.grid_columnconfigure(1, weight=1)

        wallpaper = ctk.CTkLabel(main, text="", image=self._wallpaper_image)
        wallpaper.place(relx=0.5, rely=0.5, anchor="center")

        self.lbl_target = ctk.CTkLabel(
            main, text="Select a DM or channel", anchor="w", text_color=C["text"],
            fg_color=self.panel_tint, corner_radius=14,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
        )
        self.lbl_target.grid(row=0, column=0, sticky="w", padx=(12, 4), pady=(8, 4), ipadx=14, ipady=6)

        self.btn_select_all = ctk.CTkButton(
            main, text="Select all", width=86, height=26, corner_radius=13,
            fg_color=C["card"], hover_color=C["hover"], text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=11), command=self.select_all,
        )
        self.btn_select_all.grid(row=0, column=2, padx=4, pady=(8, 4))

        self.btn_select_none = ctk.CTkButton(
            main, text="Deselect all", width=86, height=26, corner_radius=13,
            fg_color=C["card"], hover_color=C["hover"], text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=11), command=self.select_none,
        )
        self.btn_select_none.grid(row=0, column=3, padx=4, pady=(8, 4))

        self.lbl_counts = ctk.CTkLabel(
            main, text="", text_color=C["text_muted"],
            fg_color=self.panel_tint, corner_radius=14,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.lbl_counts.grid(row=0, column=4, sticky="e", padx=(4, 12), pady=(8, 4), ipadx=12, ipady=6)

        self.timeline = ctk.CTkScrollableFrame(
            main, corner_radius=14, fg_color=self.panel_tint,
            scrollbar_button_color=C["hover"], scrollbar_button_hover_color=C["text_muted"],
        )
        self.timeline.grid(row=1, column=0, columnspan=5, sticky="nsew", padx=12, pady=4)
        self.timeline.grid_columnconfigure(0, weight=1)

        self._show_empty_state()

        bottom = ctk.CTkFrame(main, corner_radius=14, fg_color=self.panel_tint)
        bottom.grid(row=2, column=0, columnspan=5, sticky="ew", padx=12, pady=(4, 12))
        bottom.grid_columnconfigure(1, weight=1)

        self.lbl_log = ctk.CTkLabel(
            bottom, text="Ready.", anchor="w", text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.lbl_log.grid(row=0, column=0, sticky="ew", padx=14, pady=14)

        self.bottom_progress = ctk.CTkProgressBar(
            bottom, height=4, width=160, progress_color=C["primary"], fg_color=C["input"]
        )
        self.bottom_progress.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        self.bottom_progress.set(0)
        self.bottom_progress.grid_remove()

        self.btn_stop = ctk.CTkButton(
            bottom, text="STOP", width=74, height=34, corner_radius=8,
            fg_color=C["danger"], hover_color=C["danger_hover"],
            state="disabled", command=self.request_stop,
        )
        self.btn_stop.grid(row=0, column=2, padx=(0, 8), pady=11)

        self.btn_delete = ctk.CTkButton(
            bottom, text="DELETE SELECTED", width=180, height=34, corner_radius=8,
            fg_color=C["danger"], hover_color=C["danger_hover"], state="disabled",
            font=ctk.CTkFont(family=FONT, size=12, weight="bold"), command=self._arm_deletion,
        )
        self.btn_delete.grid(row=0, column=3, padx=(0, 14), pady=11)

    def _show_empty_state(self):
        self.empty_state = ctk.CTkFrame(self.timeline, fg_color="transparent")
        self.empty_state.pack(fill="x", pady=(110, 0))
        ctk.CTkLabel(
            self.empty_state, text="Welcome to Discord Bulk Delete Tool",
            text_color=C["text"], font=ctk.CTkFont(family=FONT, size=24, weight="bold"),
        ).pack()
        ctk.CTkLabel(
            self.empty_state,
            text="Pick a DM or a channel from the sidebar — your messages appear here.\n"
                 "Select with click / drag / Ctrl+A, then press DELETE and confirm with ENTER.",
            text_color=C["text_muted"], justify="center",
            font=ctk.CTkFont(family=FONT, size=13),
        ).pack(pady=8)

    # --- Small helpers -----------------------------------------------------------

    def _set_status(self, text, color=None):
        self.lbl_log.configure(text=text, text_color=color or C["text_muted"])

    def log(self, text):
        self._set_status(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        print_info(text)

    def _on_time_range_change(self, value):
        if value == "Custom dates…":
            self.dates_block.pack(fill="x", padx=0, pady=(0, 2))
        else:
            self.dates_block.pack_forget()

    def request_stop(self):
        if self.is_scanning or self.is_deleting:
            self.stop_event.set()
            self.log("Stopping after the current operation…")

    def _set_busy(self, busy):
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.btn_scan.configure(state="disabled" if busy else "normal")
        self.btn_login.configure(state="disabled" if busy else "normal")

    def _show_progress(self, show):
        if show:
            self.sidebar_progress.pack(side="bottom", fill="x", padx=16, pady=(0, 10))
        else:
            self.sidebar_progress.pack_forget()

    def _validate_dates(self):
        min_id = max_id = None
        after_raw = self.entry_after.get().strip()
        if after_raw:
            min_id = date_to_snowflake(after_raw)
            if min_id is None:
                messagebox.showerror("Invalid date", f"'{after_raw}' is not a valid date (YYYY-MM-DD).")
                return None
        before_raw = self.entry_before.get().strip()
        if before_raw:
            max_id = date_to_snowflake(before_raw, end_of_day=True)
            if max_id is None:
                messagebox.showerror("Invalid date", f"'{before_raw}' is not a valid date (YYYY-MM-DD).")
                return None
        return min_id, max_id

    # --- Keyboard: Ctrl+A / DELETE arms / ENTER confirms / ESC cancels ----------

    def _bind_keyboard(self):
        self.bind_all("<Control-a>", self._on_ctrl_a)
        self.bind_all("<Delete>", self._on_delete_key)
        self.bind_all("<Return>", self._on_return_key)
        self.bind_all("<KP_Enter>", self._on_return_key)
        self.bind_all("<Escape>", self._on_escape_key)
        self.bind_all("<ButtonRelease-1>", self._on_mouse_release, add="+")

    def _on_ctrl_a(self, event):
        if self._focus_in_entry():
            with contextlib.suppress(Exception):
                event.widget.select_range(0, "end")
            return
        self.select_all()

    def _on_delete_key(self, event):
        if self._focus_in_entry():
            return  # let the entry handle text deletion
        self._arm_deletion()

    def _on_return_key(self, _event):
        if self.armed:
            self._confirm_deletion()

    def _on_escape_key(self, _event):
        if self.armed:
            self._disarm_deletion()

    def _focus_in_entry(self):
        try:
            focus = self.focus_get()
        except (KeyError, RuntimeError):
            return False
        return focus is not None and "entry" in focus.winfo_class().lower()

    def _on_mouse_release(self, _event):
        self._sweeping = False
        self._sweep_pending = False

    # --- Auth -----------------------------------------------------------------------

    def login(self):
        if self.is_scanning or self.is_deleting:
            self.log("Wait for the current operation to finish.")
            return
        token = self.entry_token.get().strip()
        if not token:
            self.lbl_userstatus.configure(text="Token required", text_color=C["danger"])
            return

        self.btn_login.configure(state="disabled", text="...")

        def run_auth():
            client = DiscordClient(token)
            try:
                user = client.validate_token()
            except (DiscordAPIError, AuthenticationError, NetworkError):
                user = None
            if user:
                self._post(lambda: self.on_login_success(client, token, user))
            else:
                client.close()
                self._post(self.on_login_fail)

        threading.Thread(target=run_auth, daemon=True).start()

    def on_login_success(self, client, token, user):
        if self.client is not None:
            self.client.close()
        self.client = client
        self.token = token
        self.logged_in_user = user
        self.session_deleted = 0
        self.deleter = MessageDeleter(client, self.app_settings)
        self.btn_login.configure(state="normal", text="Logged in", fg_color=C["success"])
        self._refresh_user_panel()
        self._update_counts()
        self._set_status(
            f"Logged in as {display_username(user)} — loading your servers and DMs…"
        )
        self._load_servers()

    def _load_servers(self):
        """Fetch joined servers + DM list in the background."""

        def run():
            guild_error = None
            try:
                guilds = self.client.fetch_guilds()
            except DiscordAPIError as exc:
                guild_error = str(exc)
                guilds = []

            dm_error = None
            try:
                dms = self.client.fetch_dm_channels()
            except DiscordAPIError as exc:
                dm_error = str(exc)
                dms = []

            if guild_error:
                self._post(lambda: self.log(f"Could not load servers: {guild_error}"))
            if dm_error:
                self._post(lambda: self.log(f"Could not load DMs: {dm_error}"))

            def apply():
                self.guilds = guilds
                self.dm_channels = dms
                self._populate_guild_rail()
                self._show_home()
                if dms:
                    # Zero-click start: scan every DM right after login.
                    self._select_channel("__all_dms__", "All Direct Messages", "aggregate")
                    self._set_status(
                        f"Loaded {len(guilds)} servers and {len(dms)} DMs — "
                        "scanning all your DMs…"
                    )
                else:
                    self._set_status(f"Loaded {len(guilds)} servers, no DMs found.")

            self._post(apply)

        threading.Thread(target=run, daemon=True).start()

    def on_login_fail(self):
        self.btn_login.configure(state="normal", text="Login", fg_color=C["primary"])
        self.lbl_userstatus.configure(text="Invalid or expired token", text_color=C["danger"])
        self.log("Authentication failed")

    def logout(self):
        if self.is_scanning or self.is_deleting:
            self.log("Wait for the current operation to finish.")
            return
        if self.client is not None:
            self.client.close()
        self.client = None
        self.deleter = None
        self.logged_in_user = None
        self.token = ""
        self.session_deleted = 0
        self.guilds = []
        self.dm_channels = []
        self.guild_channels = {}
        self.current_context = None
        for widget in self.rail_guilds.winfo_children():
            widget.destroy()
        self._guild_widgets.clear()
        self._clear_channel_list()
        self.entry_token.delete(0, "end")
        self.btn_login.configure(text="Login", fg_color=C["primary"])
        self.lbl_userstatus.configure(text="Paste a token to begin", text_color=C["text_muted"])
        self.lbl_context.configure(text="Bulk Delete Tool")
        self.lbl_target.configure(text="Select a DM or channel")
        self._refresh_user_panel()
        self._update_counts()
        self._set_status("Not logged in — paste your token to begin.")

    def _refresh_user_panel(self):
        user = self.logged_in_user
        if user:
            name = display_username(user)
            self.lbl_username.configure(text=name, text_color=C["text"])
            self.lbl_userstatus.configure(text="Online — ready to clean", text_color=C["online"])
        else:
            name = None
            self.lbl_username.configure(text="Not logged in", text_color=C["text"])
            self.lbl_userstatus.configure(text="Paste a token to begin", text_color=C["text_muted"])
        self.panel_avatar.configure(image=avatar_ctk_image(name, 36))

    def auto_find_token(self):
        self.btn_auto_token.configure(state="disabled", text="...")
        self.log("Searching the local Discord app for tokens…")

        def run_search():
            error_message = None
            try:
                tokens = find_tokens()
            except Exception as exc:
                tokens = []
                error_message = str(exc)
            if error_message is not None:
                self._post(lambda: self.log(f"Token search failed: {error_message}"))
            self._post(lambda: self.on_tokens_found(tokens))

        threading.Thread(target=run_search, daemon=True).start()

    def on_tokens_found(self, tokens):
        self.btn_auto_token.configure(state="normal", text="Auto-Find")
        if not tokens:
            messagebox.showinfo(
                "No Tokens",
                "No plaintext Discord tokens found in the local Discord app.\n"
                "(Newer Discord versions encrypt it.)\n\nPlease paste your token manually.",
            )
            return
        if len(tokens) == 1:
            self._use_found_token(tokens[0][0], tokens[0][1])
        else:
            self.show_token_selector(tokens)

    def _use_found_token(self, token, source):
        self.entry_token.delete(0, "end")
        self.entry_token.insert(0, token)
        self.log(f"Found token from {source} — press Login.")

    def show_token_selector(self, tokens):
        selector = ctk.CTkToplevel(self)
        selector.title("Select Token")
        selector.geometry("460x360")
        selector.transient(self)
        selector.grab_set()
        selector.configure(fg_color=C["sidebar"])

        x = self.winfo_x() + (self.winfo_width() - 460) // 2
        y = self.winfo_y() + (self.winfo_height() - 360) // 2
        selector.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            selector, text="Select Account", font=ctk.CTkFont(family=FONT, size=16, weight="bold")
        ).pack(pady=16)

        scroll = ctk.CTkScrollableFrame(selector, fg_color=C["list"], corner_radius=12)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        def select(token, source):
            self._use_found_token(token, source)
            selector.destroy()

        for token, source in tokens:
            row = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=10)
            row.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(row, text="", image=avatar_ctk_image(None, 30)).pack(
                side="left", padx=(10, 6), pady=8
            )
            ctk.CTkLabel(
                row, text=source, font=ctk.CTkFont(family=FONT, size=12, weight="bold"),
                text_color=C["text"],
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=token[:14] + "…", text_color=C["text_faint"],
                font=ctk.CTkFont(family=FONT, size=11),
            ).pack(side="left", padx=8)
            ctk.CTkButton(
                row, text="Select", width=74, height=26, corner_radius=13,
                fg_color=C["primary"], hover_color=C["primary_hover"],
                command=lambda t=token, s=source: select(t, s),
            ).pack(side="right", padx=10)

    # --- Context: home (DMs) / guild channels ----------------------------------

    def _show_home(self):
        self.current_context = {"kind": "home", "channel": None}
        self._set_rail_selection(None, home=True)
        self.lbl_context.configure(text="Direct Messages")
        self._render_dm_list()
        self._set_title(None)
        self._clear_timeline()

    def _select_guild(self, guild):
        if self.is_scanning or self.is_deleting:
            self.log("Wait for the current operation to finish.")
            return
        self.current_context = {"kind": "guild", "guild_id": guild["id"], "channel": None}
        self._set_rail_selection(guild["id"])
        self.lbl_context.configure(text=guild["name"])
        self._set_title(None)
        self._clear_timeline()

        if guild["id"] in self.guild_channels:
            self._render_guild_channels(guild)
            return

        self._clear_channel_list()
        self.lbl_channels_hint.configure(text="TEXT CHANNELS")
        ctk.CTkLabel(
            self.channel_list, text="Loading channels…",
            text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=11),
        ).pack(anchor="w", padx=8, pady=6)

        def run():
            error_message = None
            try:
                channels = self.client.fetch_guild_channels(guild["id"])
            except DiscordAPIError as exc:
                error_message = str(exc)
                channels = []
            if error_message:
                self._post(lambda: self.log(f"Could not load channels: {error_message}"))

            def apply():
                # Cache regardless; only re-render if this guild is still open.
                self.guild_channels[guild["id"]] = channels
                if (self.current_context or {}).get("guild_id") == guild["id"]:
                    self._render_guild_channels(guild)

            self._post(apply)

        threading.Thread(target=run, daemon=True).start()

    def _set_rail_selection(self, guild_id, home=False):
        self.btn_home.configure(
            fg_color=C["primary"] if home else C["primary"],
        )
        if home:
            self.home_pill.place(x=0, rely=0.5, anchor="w", in_=self.btn_home)
        else:
            self.home_pill.place_forget()
        for gid, widgets in self._guild_widgets.items():
            selected = gid == guild_id
            widgets["btn"].configure(fg_color=C["primary"] if selected else C["card"])
            if selected:
                widgets["pill"].place(x=0, rely=0.5, anchor="w")
            else:
                widgets["pill"].place_forget()

    def _render_dm_list(self):
        self._clear_channel_list()
        self.lbl_channels_hint.configure(text="DIRECT MESSAGES")
        if not self.client:
            message = "Log in to see your DMs."
        elif not self.dm_channels:
            message = "No DMs found."
        else:
            message = None
        if message:
            ctk.CTkLabel(
                self.channel_list, text=message,
                text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=11),
            ).pack(anchor="w", padx=8, pady=6)
            return
        self._add_channel_row(
            channel_id="__all_dms__", label="All Direct Messages", kind="aggregate",
            channel={"id": "__all_dms__", "name": "All Direct Messages", "kind": "aggregate"},
            avatar_name=None, avatar_url=None, bold=True,
        )
        for dm in self.dm_channels:
            self._add_channel_row(
                channel_id=dm["id"], label=dm["name"], kind="dm", channel=dm,
                avatar_name=dm["name"],
                avatar_url=theme.user_avatar_url(
                    (dm.get("recipient") or {}).get("id", ""),
                    (dm.get("recipient") or {}).get("avatar"),
                ),
            )

    def _render_guild_channels(self, guild):
        self._clear_channel_list()
        self.lbl_channels_hint.configure(text="TEXT CHANNELS")
        channels = self.guild_channels.get(guild["id"], [])
        if not channels:
            ctk.CTkLabel(
                self.channel_list, text="No text channels found.",
                text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=11),
            ).pack(anchor="w", padx=8, pady=6)
            return
        self._add_channel_row(
            channel_id=f"__all_channels_{guild['id']}__", label="All Channels",
            kind="aggregate",
            channel={"id": f"__all_channels_{guild['id']}__", "name": "All Channels",
                     "kind": "aggregate"},
            avatar_name=None, avatar_url=None, bold=True,
        )
        for channel in channels:
            self._add_channel_row(
                channel_id=channel["id"], label=channel.get("name", "channel"),
                kind="guild", channel=channel, avatar_name=None, avatar_url=None,
            )

    def _clear_channel_list(self):
        for widget in self.channel_list.winfo_children():
            widget.destroy()
        self._channel_rows.clear()

    def _add_channel_row(self, channel_id, label, kind, channel, avatar_name, avatar_url, bold=False):
        prefix = "⚡  " if kind == "aggregate" else ("@  " if kind == "dm" else "#  ")
        image = None
        if kind == "dm":
            image = (
                cdn_ctk_image(avatar_url, avatar_name, 26)
                if avatar_url else avatar_ctk_image(avatar_name, 26)
            )
        row = ctk.CTkButton(
            self.channel_list,
            text=f" {prefix}{label}" if image is None else f" {label}",
            image=image, compound="left", anchor="w", height=36, corner_radius=8,
            fg_color="transparent", hover_color=C["hover"], text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=12, weight="bold" if bold else "normal"),
            command=lambda: self._select_channel(channel_id, label, kind),
        )
        row.pack(fill="x", pady=1)
        self._channel_rows[channel_id] = row

    def _select_channel(self, channel_id, label, kind):
        if self.is_scanning or self.is_deleting:
            self.log("Wait for the current operation to finish.")
            return
        self.current_context = dict(self.current_context or {})
        self.current_context["channel"] = {"id": channel_id, "name": label, "kind": kind}
        for cid, row in self._channel_rows.items():
            selected = cid == channel_id
            row.configure(
                fg_color=C["primary"] if selected else "transparent",
                text_color="#ffffff" if selected else C["text_muted"],
                hover_color=C["primary_hover"] if selected else C["hover"],
            )
        self._set_title({"name": label, "kind": kind})
        self._scan_current()  # Discord-style: click a channel, messages appear

    def _set_title(self, channel):
        if channel is None:
            self.lbl_target.configure(text="Select a DM or channel")
            return
        if channel["kind"] == "dm":
            self.lbl_target.configure(text=f"@ {channel['name']}")
        elif channel["kind"] == "aggregate":
            at_home = (self.current_context or {}).get("kind") == "home"
            self.lbl_target.configure(text=f"{'@' if at_home else '#'} {channel['name']}")
        else:
            self.lbl_target.configure(text=f"# {channel['name']}")

    # --- Message stream (batched rendering) ------------------------------------------

    def _queue_rows(self, new_msgs, generation):
        # Stale-guard: a scan that was cleared mid-flight must never render.
        self._post(lambda: self._schedule_rows(new_msgs, generation))

    def _schedule_rows(self, new_msgs, generation):
        if generation != self._scan_generation:
            return  # batch belongs to a superseded scan
        if getattr(self, "empty_state", None) is not None and self.empty_state.winfo_exists():
            self.empty_state.destroy()
            self.empty_state = None
        self.scanned_messages.extend(new_msgs)
        self._pending_rows.extend(new_msgs)
        if not self._row_flush_scheduled:
            self._row_flush_scheduled = True
            self.after(ROW_FLUSH_DELAY_MS, self._flush_rows)

    def _flush_rows(self):
        batch = self._pending_rows[:ROW_BATCH_SIZE]
        self._pending_rows = self._pending_rows[ROW_BATCH_SIZE:]
        for msg in batch:
            self._add_message_row(msg)
        self._update_counts()
        if self._pending_rows:
            self.after(ROW_FLUSH_DELAY_MS, self._flush_rows)
        else:
            self._row_flush_scheduled = False

    def _clear_timeline(self):
        self._scan_generation += 1  # invalidates batches still in the queue
        self.scanned_messages = []
        self._pending_rows = []
        self._row_flush_scheduled = False
        self._last_rendered_msg = None
        for widget in self.timeline.winfo_children():
            widget.destroy()
        self.selected_ids.clear()
        self.check_vars.clear()
        self.rows.clear()
        self._update_counts()
        self._show_empty_state()

    def _is_grouped_with_previous(self, msg):
        """Discord-style grouping: same channel within ~7 minutes of the
        previously rendered (newer) message → compact row without header."""
        previous = self._last_rendered_msg
        self._last_rendered_msg = msg
        if previous is None or previous.get("channel_id") != msg.get("channel_id"):
            return False
        newer = parse_date(previous.get("timestamp"))
        older = parse_date(msg.get("timestamp"))
        if newer is None or older is None:
            return False
        gap = (newer - older).total_seconds()
        return 0 <= gap <= GROUP_WINDOW_SECONDS

    def _add_message_row(self, msg):
        grouped = self._is_grouped_with_previous(msg)
        row = ctk.CTkFrame(self.timeline, fg_color=self.panel_tint, corner_radius=8)
        row.pack(fill="x", padx=2, pady=1)

        username = self.logged_in_user["username"] if self.logged_in_user else "You"
        var = ctk.BooleanVar(value=False)
        self.check_vars[msg["id"]] = var
        self.rows[msg["id"]] = row

        # Attachment-only messages have no body text; a grouped row with no
        # content would render as a blank strip, so force a full header.
        grouped = grouped and bool(msg.get("content"))

        def on_toggle():
            if var.get():
                self.selected_ids.add(msg["id"])
            else:
                self.selected_ids.discard(msg["id"])
            self._update_counts()

        # Right cluster first (so it never gets squeezed out): checkbox + hover ID
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", padx=10)
        id_label = ctk.CTkLabel(
            right, text="", text_color=C["text_faint"],
            font=ctk.CTkFont(family="Consolas", size=9), height=12,
        )
        id_label.pack(anchor="e")
        checkbox = ctk.CTkCheckBox(
            right, text="", width=24, variable=var, command=on_toggle,
            checkbox_width=18, checkbox_height=18, corner_radius=5,
            border_color=C["text_faint"], fg_color=C["primary"], hover_color=C["primary_hover"],
        )
        checkbox.pack(anchor="e", pady=(2, 0))

        if grouped:
            # height=1: CTkFrame defaults to 200px, which would inflate the row
            spacer = ctk.CTkFrame(row, width=46, height=1, fg_color="transparent")
            spacer.pack(side="left", fill="y")
        else:
            ctk.CTkLabel(row, text="", image=avatar_ctk_image(username, 30)).pack(
                side="left", padx=(10, 8), pady=8
            )

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, pady=(6, 6) if not grouped else (1, 4))

        if not grouped:
            top = ctk.CTkFrame(info, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(
                top, text=username, text_color=C["text"],
                font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            ).pack(side="left")
            ctk.CTkLabel(
                top, text=format_timestamp_compact(msg.get("timestamp")),
                text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=11),
            ).pack(side="left", padx=8)
            if msg.get("attachments"):
                ctk.CTkLabel(
                    top, text=" attachment ", corner_radius=6, fg_color=C["card"],
                    text_color=C["accent"], font=ctk.CTkFont(family=FONT, size=10),
                ).pack(side="left", padx=4)

        # The green chip in the header already signals attachments — only show
        # actual text in the body, never a duplicate "[Attachment]" placeholder.
        content = msg.get("content", "")
        ctk.CTkLabel(
            info, text=content, anchor="w", justify="left", wraplength=640,
            text_color=C["content"], font=ctk.CTkFont(family=FONT, size=12),
        ).pack(fill="x")

        self._bind_row_hover(row, msg["id"], id_label)
        row._dbdt_msg_id = msg["id"]  # sweep drag resolves rows through this tag
        self._wire_row_clicks(row, checkbox, msg["id"], var, on_toggle)

    def _wire_row_clicks(self, row, exclude, msg_id, var, on_toggle):
        """Click-to-toggle plus press-drag sweep selection, wired across every
        child except the checkbox. Drag tracking resolves the row under the
        pointer with winfo_containing, because tkinter's implicit button grab
        stops <Enter> events from reaching the other rows during a drag."""

        def press(_event):
            self._sweep_pending = True
            self._sweep_start = msg_id

        def drag(event):
            if not self._sweep_pending and not self._sweeping:
                return
            self._sweeping = True
            self._select_row(self._sweep_start, True)
            try:
                under = row.winfo_containing(event.x_root, event.y_root)
            except (KeyError, RuntimeError):
                return
            while under is not None:
                tagged = getattr(under, "_dbdt_msg_id", None)
                if tagged is not None:
                    self._select_row(tagged, True)
                    return
                parent = under.winfo_parent()
                under = row.nametowidget(parent) if parent else None

        def click(_event):
            if self._sweeping:
                return
            var.set(not var.get())
            on_toggle()

        stack = [row]
        while stack:
            widget = stack.pop()
            if widget is exclude:
                continue
            with contextlib.suppress(Exception):
                widget.bind("<Button-1>", press, add="+")
                widget.bind("<B1-Motion>", drag, add="+")
                widget.bind("<Button-1>", click, add="+")
            stack.extend(widget.winfo_children())

    def _on_row_enter_sweep(self, msg_id):
        if self._sweeping:
            self._select_row(msg_id, True)

    def _bind_row_hover(self, row, msg_id, id_label):
        """Hover tint, sweep-select on entry, reveal the message ID."""

        def set_hover(active):
            row.configure(fg_color=C["hover"] if active else self.panel_tint)
            id_label.configure(text=msg_id if active else "")

        def pointer_inside():
            try:
                widget = row.winfo_containing(row.winfo_pointerx(), row.winfo_pointery())
            except (KeyError, RuntimeError):
                return False
            while widget is not None:
                if widget is row:
                    return True
                parent = widget.winfo_parent()
                widget = row.nametowidget(parent) if parent else None
            return False

        def enter(_event=None):
            set_hover(True)
            self._on_row_enter_sweep(msg_id)

        def leave(_event=None):
            row.after(80, lambda: set_hover(pointer_inside()))

        stack = [row]
        while stack:
            widget = stack.pop()
            with contextlib.suppress(Exception):
                widget.bind("<Enter>", enter)
                widget.bind("<Leave>", leave)
            stack.extend(widget.winfo_children())

    # --- Scanning ------------------------------------------------------------------------

    def _scan_current(self):
        if not self.client or not self.deleter:
            messagebox.showerror("Error", "Please login first")
            return
        if self.is_scanning or self.is_deleting:
            self.log("Already busy — press STOP to cancel the current operation.")
            return
        channel = (self.current_context or {}).get("channel")
        if not channel:
            self.log("Pick a DM or a channel from the sidebar first.")
            return

        preset = self.time_range_var.get()
        min_id = max_id = None
        if preset in TIME_WINDOWS:
            min_id = relative_snowflake(TIME_WINDOWS[preset])
        elif preset == "Custom dates…":
            dates = self._validate_dates()
            if dates is None:
                return
            min_id, max_id = dates

        self.stop_event = threading.Event()
        self.is_scanning = True
        self._set_busy(True)
        self._show_progress(True)
        self.sidebar_progress.configure(mode="indeterminate")
        self.sidebar_progress.start()
        self._set_status(f"Scanning {preset.lower()}…")

        self._clear_timeline()
        generation = self._scan_generation

        # The history endpoint covers both DMs and guild text channels.
        query = self.entry_filter.get().strip() or None

        if channel.get("kind") == "aggregate":
            # Sweep every channel of the current context in one pass.
            at_home = (self.current_context or {}).get("kind") == "home"
            targets = list(self.dm_channels) if at_home else list(
                self.guild_channels.get((self.current_context or {}).get("guild_id"), [])
            )
            targets = [t for t in targets if t.get("id")]
            label = "DMs" if at_home else "channels"
        else:
            targets = [channel]
            label = None

        def run_scan():
            errors = []
            collected = []
            for index, target in enumerate(targets):
                if self.stop_event.is_set():
                    break
                if label:
                    name = target.get("name", "channel")
                    status_text = (
                        f"Scanning {label}: {len(targets) - index} left — {name}…"
                    )
                    self._post(lambda text=status_text: self._set_status(text))
                try:
                    msgs, scan_errors = self.deleter.scan_messages(
                        context_id=target["id"],
                        is_dm=True,
                        content_query=query,
                        min_id=min_id,
                        max_id=max_id,
                        progress_callback=lambda batch: self._queue_rows(batch, generation),
                        stop_event=self.stop_event,
                    )
                    collected.extend(msgs)
                    errors.extend(scan_errors)
                except AuthenticationError:
                    self._post(self._on_token_invalid)
                    return
                except Exception as exc:
                    error_message = str(exc)
                    errors.append(error_message)
                    self._post(lambda m=error_message: self.log(f"Scan failed: {m}"))

            def finish():
                if label and self.stop_event.is_set():
                    errors.append("Scan cancelled by user.")
                self.on_scan_complete(collected, errors, generation)

            self._post(finish)

        threading.Thread(target=run_scan, daemon=True).start()

    def on_scan_complete(self, msgs, errors, generation):
        if generation != self._scan_generation:
            return  # a newer scan/clear superseded this one
        self.stop_loading_ui()
        for error in errors:
            self.log(f"Search reported: {error}")
        if msgs:
            self._set_status(
                f"Scan complete — {len(msgs)} messages found. Select the ones to delete."
            )
        else:
            self._set_status("No messages found for the selected filters.")

    def on_scan_error(self, message, generation):
        if generation != self._scan_generation:
            return
        self.stop_loading_ui()
        self.log(f"Scan failed: {message}")

    def _on_token_invalid(self):
        self.stop_loading_ui()
        self.lbl_userstatus.configure(text="Token invalid/expired", text_color=C["danger"])
        self.btn_login.configure(text="Login", fg_color=C["primary"], state="normal")
        self.log("Your token stopped working — paste a fresh one and log in again.")

    def stop_loading_ui(self):
        self.is_scanning = False
        self.btn_scan.configure(state="normal", text="SCAN MESSAGES")
        self.btn_login.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.sidebar_progress.stop()
        self.sidebar_progress.configure(mode="determinate")
        self.sidebar_progress.set(0)
        self._show_progress(False)

    # --- Deletion: DELETE arms → ENTER confirms → ESC cancels -----------------------------

    def _arm_deletion(self):
        if self.is_scanning or self.is_deleting or self.armed:
            return
        if not self.selected_ids:
            self._set_status("Nothing selected — click rows, drag over them, or press Ctrl+A.")
            return
        count = len(self.selected_ids)
        if not self.app_settings.get("confirm_before_delete", True):
            self._start_deletion()
            return
        self.armed = True
        self.lbl_log.configure(
            text=f"⚠  Press ENTER to permanently delete {count} messages — ESC to cancel",
            text_color=C["danger"],
        )
        self.btn_delete.configure(text=f"PRESS ENTER ⏎ ({count})")
        self.focus_set()

    def _disarm_deletion(self):
        if not self.armed:
            return
        self.armed = False
        self._update_counts()
        self._set_status("Deletion cancelled.")

    def _confirm_deletion(self):
        if not self.armed:
            return
        self.armed = False
        self._set_status("Deleting…")
        self._start_deletion()

    def _start_deletion(self):
        if self.is_scanning or self.is_deleting:
            self.log("Already busy.")
            return
        msgs_to_del = [m for m in self.scanned_messages if m["id"] in self.selected_ids]
        if not msgs_to_del:
            return

        self.stop_event = threading.Event()
        self.is_deleting = True
        self.btn_delete.configure(state="disabled", text="Deleting…")
        self.btn_scan.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._show_progress(True)
        self.bottom_progress.set(0)

        def run_del():
            try:
                result = self.deleter.execute_deletion(
                    msgs_to_del,
                    skip_confirm=True,
                    show_progress=False,
                    stop_event=self.stop_event,
                    progress_callback=lambda d, f, t: self.after(
                        0, lambda: self.update_delete_status(d, f, t)
                    ),
                )
                self._post(lambda: self.on_del_complete(result))
            except AuthenticationError:
                self._post(self._on_token_invalid)
            except Exception as exc:
                error_message = str(exc)
                self._post(lambda: self.on_del_error(error_message))

        threading.Thread(target=run_del, daemon=True).start()

    def update_delete_status(self, deleted, failed, total):
        if total > 0:
            self.bottom_progress.set((deleted + failed) / total)
        self._set_status(f"Deleting: {deleted}/{total} (failed: {failed})", color=C["danger"])

    def on_del_complete(self, result):
        self.is_deleting = False
        self._show_progress(False)
        self.btn_stop.configure(state="disabled")
        self.btn_scan.configure(state="normal")
        self.session_deleted += result["deleted"]

        deleted_ids = set(result["deleted_ids"])
        self.scanned_messages = [m for m in self.scanned_messages if m["id"] not in deleted_ids]
        for msg_id in deleted_ids:
            row = self.rows.pop(msg_id, None)
            if row is not None:
                row.destroy()
            self.check_vars.pop(msg_id, None)
            self.selected_ids.discard(msg_id)
        self._update_counts()
        self.btn_delete.configure(state="disabled", text="DELETE SELECTED")

        summary = f"Deleted {result['deleted']}, failed {result['failed']}."
        if result["cancelled"]:
            summary += " (stopped early — remaining messages untouched)"
        self._set_status(f"{summary} Total deleted this session: {self.session_deleted}.")

    def on_del_error(self, message):
        self.is_deleting = False
        self._show_progress(False)
        self.btn_stop.configure(state="disabled")
        self.btn_scan.configure(state="normal")
        self.btn_delete.configure(state="normal", text="DELETE SELECTED")
        self.log(f"Deletion failed: {message}")

    # --- Selection -------------------------------------------------------------------------

    def _select_row(self, msg_id, value):
        var = self.check_vars.get(msg_id)
        if var is None:
            return
        var.set(value)
        if value:
            self.selected_ids.add(msg_id)
        else:
            self.selected_ids.discard(msg_id)
        self._update_counts()

    def select_all(self):
        for msg_id in self.check_vars:
            self.check_vars[msg_id].set(True)
        self.selected_ids = {m["id"] for m in self.scanned_messages}
        self._update_counts()

    def select_none(self):
        for var in self.check_vars.values():
            var.set(False)
        self.selected_ids.clear()
        self._update_counts()

    def _update_counts(self):
        found = len(self.scanned_messages)
        selected = len(self.selected_ids)
        self.lbl_counts.configure(text=f"{found} found • {selected} selected")
        self.stat_found.configure(text=str(found))
        self.stat_selected.configure(text=str(selected))
        self.stat_deleted.configure(text=str(self.session_deleted))
        if self.armed:
            self.btn_delete.configure(state="normal", text=f"PRESS ENTER ⏎ ({selected})")
        elif selected > 0 and not self.is_deleting:
            self.btn_delete.configure(state="normal", text=f"DELETE ({selected})")
        else:
            self.btn_delete.configure(state="disabled", text="DELETE SELECTED")

    # --- Settings dialog -----------------------------------------------------------------------

    def show_settings(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Settings")
        dialog.geometry("430x430")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=C["sidebar"])

        x = self.winfo_x() + (self.winfo_width() - 430) // 2
        y = self.winfo_y() + (self.winfo_height() - 430) // 2
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            dialog, text="Settings", font=ctk.CTkFont(family=FONT, size=17, weight="bold"),
            text_color=C["text"],
        ).pack(pady=(16, 2))
        ctk.CTkLabel(
            dialog,
            text="Slower delays are safer (Discord ToS). Saved to settings.json.",
            text_color=C["text_muted"], font=ctk.CTkFont(family=FONT, size=11),
        ).pack(pady=(0, 10))

        form = ctk.CTkFrame(dialog, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20)
        form.grid_columnconfigure(1, weight=1)

        fields = [
            ("Delete delay min (s)", "delete_delay_min"),
            ("Delete delay max (s)", "delete_delay_max"),
            ("Scan delay min (s)", "scan_delay_min"),
            ("Scan delay max (s)", "scan_delay_max"),
            ("Abort after N failures", "max_consecutive_failures"),
        ]
        entries = {}
        for row_index, (label, key) in enumerate(fields):
            ctk.CTkLabel(
                form, text=label, anchor="w", text_color=C["text"],
                font=ctk.CTkFont(family=FONT, size=12),
            ).grid(row=row_index, column=0, sticky="ew", padx=(0, 10), pady=6)
            entry = ctk.CTkEntry(
                form, height=30, fg_color=C["input"], border_color=C["header"],
                text_color=C["text"],
            )
            entry.insert(0, str(self.app_settings.get(key, "")))
            entry.grid(row=row_index, column=1, sticky="ew", pady=6)
            entries[key] = entry

        confirm_var = ctk.BooleanVar(value=bool(self.app_settings.get("confirm_before_delete", True)))
        ctk.CTkCheckBox(
            form, text="Ask for confirmation before deleting",
            variable=confirm_var, text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=12), fg_color=C["primary"],
            hover_color=C["primary_hover"],
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=12)

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 16))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)

        def save():
            for key, entry in entries.items():
                self.app_settings[key] = entry.get().strip()
            self.app_settings["confirm_before_delete"] = bool(confirm_var.get())
            self.app_settings.update(settings_store.save_settings(self.app_settings))
            if self.client is not None:
                self.deleter = MessageDeleter(self.client, self.app_settings)
            self.log("Settings saved.")
            dialog.destroy()

        def reset_defaults():
            for key, entry in entries.items():
                entry.delete(0, "end")
                entry.insert(0, str(settings_store.DEFAULTS[key]))
            confirm_var.set(settings_store.DEFAULTS["confirm_before_delete"])

        ctk.CTkButton(
            buttons, text="Save", height=32, fg_color=C["primary"],
            hover_color=C["primary_hover"], command=save,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")
        ctk.CTkButton(
            buttons, text="Reset defaults", height=32, fg_color=C["card"],
            hover_color=C["hover"], text_color=C["text_muted"], command=reset_defaults,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

    # --- About dialog (profile-card style) ----------------------------------------------------

    def show_about(self):
        about = ctk.CTkToplevel(self)
        about.title("About")
        about.geometry("380x470")
        about.resizable(False, False)
        about.transient(self)
        about.grab_set()
        about.configure(fg_color=C["sidebar"])

        banner = ctk.CTkFrame(about, height=104, corner_radius=0, fg_color=C["primary"])
        banner.pack(fill="x")
        banner.pack_propagate(False)
        ctk.CTkLabel(
            banner, text="🧹", font=ctk.CTkFont(family="Segoe UI Emoji", size=34)
        ).pack(expand=True)

        body = ctk.CTkFrame(about, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(14, 16))

        ctk.CTkLabel(
            body, text="Discord Bulk Delete Tool",
            font=ctk.CTkFont(family=FONT, size=18, weight="bold"), text_color=C["text"],
        ).pack()
        ctk.CTkLabel(
            body, text=f"Version {VERSION} • MIT License",
            font=ctk.CTkFont(family=FONT, size=11), text_color=C["text_muted"],
        ).pack(pady=(2, 10))

        playing = ctk.CTkFrame(body, fg_color=C["card"], corner_radius=10)
        playing.pack(fill="x", pady=6)
        ctk.CTkLabel(
            playing, text="PLAYING", anchor="w", text_color=C["text_faint"],
            font=ctk.CTkFont(family=FONT, size=9, weight="bold"),
        ).pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            playing, text="🧹 Cleaning up old messages", anchor="w",
            text_color=C["text"], font=ctk.CTkFont(family=FONT, size=12),
        ).pack(fill="x", padx=12, pady=(0, 8))

        credits = ctk.CTkLabel(
            body,
            text="UI design inspired by the ClearVision Discord theme\n"
                 "(github.com/ClearVision/ClearVision-v6 — Apache-2.0).\n\n"
                 "Wallpaper is generated procedurally in-app —\n"
                 "no third-party images are bundled. Supply your own\n"
                 "at assets/background.png (respect its license).",
            justify="center", text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=10),
        )
        credits.pack(pady=12)

        ctk.CTkButton(
            body, text="View on GitHub", height=32, corner_radius=8,
            fg_color=C["primary"], hover_color=C["primary_hover"],
            command=lambda: webbrowser.open("https://github.com/prafulaa/DiscordBulkDeleteTool"),
        ).pack(fill="x", pady=(4, 0))

    # --- Lifecycle ---------------------------------------------------------------------------

    def _on_close(self):
        self.stop_event.set()
        if self.client is not None:
            self.client.close()
        self.destroy()

    def main(self):  # entry point for `discord-tool-gui`
        self.mainloop()


def main():
    app = DiscordToolGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
