"""Discord-themed GUI for the Bulk Delete Tool (CustomTkinter).

Layout & visual language inspired by Discord and the ClearVision theme
(https://github.com/ClearVision/ClearVision-v6, Apache-2.0):
icon rail → channel sidebar → chat-style message list over an aurora
wallpaper that is generated procedurally at runtime (theme.py).
"""

import contextlib
import ctypes
import sys
import threading
import webbrowser
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme
from api_client import AuthenticationError, DiscordAPIError, DiscordClient, NetworkError
from deleter import MessageDeleter
from token_finder import find_tokens
from utils import (
    VERSION,
    date_to_snowflake,
    display_username,
    format_discord_timestamp,
    print_info,
)

# Crisp rendering on high-DPI Windows displays (must happen before Tk init)
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()

WALLPAPER_SIZE = (2400, 1500)
CARD_BATCH_SIZE = 40
CARD_FLUSH_DELAY_MS = 10
AVATAR_CACHE = {}

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

C = theme.COLORS
FONT = "Segoe UI"


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


class DiscordToolGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"Discord Bulk Delete Tool — v{VERSION}")
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
        self.target = "dm"  # "dm" | "guild"
        self.scanned_messages = []
        self.selected_ids = set()
        self.check_vars = {}
        self.cards = {}
        self.is_scanning = False
        self.is_deleting = False
        self.stop_event = threading.Event()
        self._pending_cards = []
        self._card_flush_scheduled = False

        self._build_wallpaper_source()
        self._build_rail()
        self._build_sidebar()
        self._build_main()
        self.select_target("dm")

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
        self._wallpaper_image = ctk.CTkImage(
            light_image=wallpaper, dark_image=wallpaper, size=WALLPAPER_SIZE
        )

    # --- Icon rail -------------------------------------------------------------

    def _build_rail(self):
        rail = ctk.CTkFrame(self, width=68, corner_radius=0, fg_color=C["rail"])
        rail.grid(row=0, column=0, sticky="nsew")
        rail.grid_rowconfigure(4, weight=1)

        logo = ctk.CTkLabel(
            rail, text="🧹", width=44, height=44, corner_radius=16,
            fg_color=C["primary"], font=ctk.CTkFont(family="Segoe UI Emoji", size=20),
        )
        logo.grid(row=0, column=0, padx=12, pady=(14, 10))

        ctk.CTkFrame(rail, height=1, width=36, fg_color=C["text_faint"]).grid(row=1, column=0, padx=16)

        btn_about = ctk.CTkButton(
            rail, text="i", width=44, height=44, corner_radius=16,
            fg_color=C["card"], hover_color=C["hover"], text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=17, weight="bold"), command=self.show_about,
        )
        btn_about.grid(row=2, column=0, padx=12, pady=10)

        btn_github = ctk.CTkButton(
            rail, text="↗", width=44, height=44, corner_radius=16,
            fg_color=C["card"], hover_color=C["hover"], text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            command=lambda: webbrowser.open("https://github.com/prafulaa/DiscordBulkDeleteTool"),
        )
        btn_github.grid(row=3, column=0, padx=12, pady=10)

        self.rail_dot = ctk.CTkLabel(rail, text="●", text_color=C["offline"], font=("Arial", 14))
        self.rail_dot.grid(row=5, column=0, padx=12, pady=16)

    # --- Sidebar -----------------------------------------------------------------

    def _section(self, parent, title):
        """'— Text channels —' style section header with decorative lines."""
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=14, pady=(16, 4))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(2, weight=1)
        ctk.CTkFrame(wrap, height=1, width=40, fg_color=C["text_faint"]).grid(
            row=0, column=0, sticky="ew"
        )
        ctk.CTkLabel(
            wrap, text=title, text_color=C["text_muted"],
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
        ).grid(row=0, column=1, padx=10)
        ctk.CTkFrame(wrap, height=1, width=40, fg_color=C["text_faint"]).grid(
            row=0, column=2, sticky="ew"
        )

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color=C["sidebar"])
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.pack_propagate(False)

        # Header strip (server-selector style)
        header = ctk.CTkFrame(sidebar, height=46, corner_radius=0, fg_color=C["header"])
        header.pack(side="top", fill="x")
        ctk.CTkLabel(
            header, text="🧹 Bulk Delete Tool", anchor="w", text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkLabel(
            header, text=f"v{VERSION}", text_color=C["text_faint"],
            font=ctk.CTkFont(family=FONT, size=11),
        ).pack(side="right", padx=14)

        # Bottom-first: user panel, then progress bar above it
        panel = ctk.CTkFrame(sidebar, height=62, corner_radius=0, fg_color=C["header"])
        panel.pack(side="bottom", fill="x")
        self.sidebar_progress = ctk.CTkProgressBar(
            sidebar, height=4, progress_color=C["primary"], fg_color=C["input"]
        )
        self.sidebar_progress.set(0)

        body = ctk.CTkFrame(sidebar, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True)

        # AUTH
        self._section(body, "AUTHENTICATION")
        self.entry_token = ctk.CTkEntry(
            body, placeholder_text="Paste your user token", show="•", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_token.pack(fill="x", padx=16, pady=(2, 8))
        self.entry_token.bind("<Return>", lambda _e: self.login())

        auth_row = ctk.CTkFrame(body, fg_color="transparent")
        auth_row.pack(fill="x", padx=16)
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

        # TARGET — channel-row style selection
        self._section(body, "TARGET")
        self.target_rows = {}
        for key, glyph, label in (("dm", "@", "Direct Messages"), ("guild", "#", "Server (Guild)")):
            row = ctk.CTkButton(
                body, text=f"  {glyph}   {label}", anchor="w", height=34,
                corner_radius=8, fg_color="transparent",
                hover_color=C["hover"], text_color=C["text_muted"],
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda k=key: self.select_target(k),
            )
            row.pack(fill="x", padx=12, pady=1)
            self.target_rows[key] = row

        # FILTERS
        self._section(body, "FILTERS")
        self.entry_id = ctk.CTkEntry(
            body, placeholder_text="Channel / Guild ID", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_id.pack(fill="x", padx=16, pady=(2, 6))
        self.entry_id.bind("<KeyRelease>", lambda _e: self._refresh_target_title())

        self.entry_filter = ctk.CTkEntry(
            body, placeholder_text="Keyword filter (optional)", height=34,
            fg_color=C["input"], border_color=C["header"], text_color=C["text"],
        )
        self.entry_filter.pack(fill="x", padx=16, pady=(0, 6))

        dates_row = ctk.CTkFrame(body, fg_color="transparent")
        dates_row.pack(fill="x", padx=16)
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
        ctk.CTkLabel(
            body, text="Dates optional — format YYYY-MM-DD", anchor="w",
            text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=10),
        ).pack(fill="x", padx=18, pady=(3, 0))

        # SCAN
        self._section(body, "SCAN")
        self.btn_scan = ctk.CTkButton(
            body, text="SCAN MESSAGES", height=38, corner_radius=8,
            fg_color=C["success"], hover_color=C["success_hover"],
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"), command=self.start_scan,
        )
        self.btn_scan.pack(fill="x", padx=16, pady=(2, 4))

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

    # --- Main area ----------------------------------------------------------------

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=C["list"])
        main.grid(row=0, column=2, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=0)  # title
        main.grid_columnconfigure(1, weight=1)  # spacer — wallpaper shows through

        wallpaper = ctk.CTkLabel(main, text="", image=self._wallpaper_image)
        wallpaper.place(relx=0.5, rely=0.5, anchor="center")

        # Top bar — widgets sit directly over the wallpaper; the gaps between
        # them stay transparent so the aurora background stays visible.
        self.lbl_target = ctk.CTkLabel(
            main, text="# direct-messages", anchor="w", text_color=C["text"],
            fg_color=C["list"], corner_radius=14,
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
            fg_color=C["list"], corner_radius=14,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.lbl_counts.grid(row=0, column=4, sticky="e", padx=(4, 12), pady=(8, 4), ipadx=12, ipady=6)

        # Message list — floating panel with wallpaper gutters around it
        self.timeline = ctk.CTkScrollableFrame(
            main, corner_radius=14, fg_color=C["list"],
            scrollbar_button_color=C["card"], scrollbar_button_hover_color=C["hover"],
        )
        self.timeline.grid(row=1, column=0, columnspan=5, sticky="nsew", padx=12, pady=4)
        self.timeline.grid_columnconfigure(0, weight=1)

        self._show_empty_state()

        # Bottom action bar — message-input style strip
        bottom = ctk.CTkFrame(main, corner_radius=14, fg_color=C["list"])
        bottom.grid(row=2, column=0, columnspan=5, sticky="ew", padx=12, pady=(4, 12))
        bottom.grid_columnconfigure(1, weight=1)

        self.lbl_log = ctk.CTkLabel(
            bottom, text="Ready — paste your token and log in to start.",
            anchor="w", text_color=C["text_muted"], font=ctk.CTkFont(family=FONT, size=12),
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
            bottom, text="DELETE SELECTED", width=170, height=34, corner_radius=8,
            fg_color=C["danger"], hover_color=C["danger_hover"], state="disabled",
            font=ctk.CTkFont(family=FONT, size=12, weight="bold"), command=self.start_delete,
        )
        self.btn_delete.grid(row=0, column=3, padx=(0, 14), pady=11)

    def _show_empty_state(self):
        self.empty_state = ctk.CTkFrame(self.timeline, fg_color="transparent")
        self.empty_state.pack(fill="x", pady=(120, 0))
        ctk.CTkLabel(
            self.empty_state, text="Welcome to Discord Bulk Delete Tool",
            text_color=C["text"], font=ctk.CTkFont(family=FONT, size=24, weight="bold"),
        ).pack()
        ctk.CTkLabel(
            self.empty_state,
            text="This is the beginning of your cleanup.\n"
                 "Log in, pick a target on the left, then press SCAN MESSAGES.",
            text_color=C["text_muted"], justify="center",
            font=ctk.CTkFont(family=FONT, size=13),
        ).pack(pady=8)

    # --- Small helpers -----------------------------------------------------------

    def log(self, text):
        self.lbl_log.configure(text=f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        print_info(text)

    def _refresh_target_title(self):
        raw_id = self.entry_id.get().strip()
        suffix = f"  ›  {raw_id}" if raw_id else ""
        name = "direct-messages" if self.target == "dm" else "server"
        glyph = "@" if self.target == "dm" else "#"
        self.lbl_target.configure(text=f"{glyph} {name}{suffix}")

    def select_target(self, key):
        self.target = key
        for row_key, row in self.target_rows.items():
            selected = row_key == key
            row.configure(
                fg_color=C["primary"] if selected else "transparent",
                text_color="#ffffff" if selected else C["text_muted"],
                hover_color=C["primary_hover"] if selected else C["hover"],
            )
        self._refresh_target_title()

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
            # side="bottom" stacking order puts this just above the user panel
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
                self.after(0, lambda: self.on_login_success(client, token, user))
            else:
                client.close()
                self.after(0, self.on_login_fail)

        threading.Thread(target=run_auth, daemon=True).start()

    def on_login_success(self, client, token, user):
        if self.client is not None:
            self.client.close()
        self.client = client
        self.token = token
        self.logged_in_user = user
        self.deleter = MessageDeleter(client)
        self.btn_login.configure(state="normal", text="Logged in", fg_color=C["success"])
        self._refresh_user_panel()
        self.log(f"Logged in as {display_username(user)}")

    def on_login_fail(self):
        self.btn_login.configure(state="normal", text="Login", fg_color=C["primary"])
        self.lbl_userstatus.configure(text="Invalid or expired token", text_color=C["danger"])
        self.rail_dot.configure(text_color=C["offline"])
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
        self.entry_token.delete(0, "end")
        self.btn_login.configure(text="Login", fg_color=C["primary"])
        self.lbl_userstatus.configure(text="Paste a token to begin", text_color=C["text_muted"])
        self.rail_dot.configure(text_color=C["offline"])
        self._refresh_user_panel()
        self.log("Logged out.")

    def _refresh_user_panel(self):
        user = self.logged_in_user
        if user:
            name = display_username(user)
            self.lbl_username.configure(text=name, text_color=C["text"])
            self.lbl_userstatus.configure(text="Online — ready to clean", text_color=C["online"])
            self.rail_dot.configure(text_color=C["online"])
        else:
            name = None
            self.lbl_username.configure(text="Not logged in", text_color=C["text"])
            self.lbl_userstatus.configure(text="Paste a token to begin", text_color=C["text_muted"])
            self.rail_dot.configure(text_color=C["offline"])
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
                self.after(0, lambda: self.log(f"Token search failed: {error_message}"))
            self.after(0, lambda: self.on_tokens_found(tokens))

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
            avatar = ctk.CTkLabel(row, text="", image=avatar_ctk_image(None, 30))
            avatar.pack(side="left", padx=(10, 6), pady=8)
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

    # --- Message timeline (batched rendering) ------------------------------------------

    def _queue_cards(self, new_msgs):
        self.after(0, lambda: self._schedule_cards(new_msgs))

    def _schedule_cards(self, new_msgs):
        if getattr(self, "empty_state", None) is not None and self.empty_state.winfo_exists():
            self.empty_state.destroy()
            self.empty_state = None
        self.scanned_messages.extend(new_msgs)
        self._pending_cards.extend(new_msgs)
        if not self._card_flush_scheduled:
            self._card_flush_scheduled = True
            self.after(CARD_FLUSH_DELAY_MS, self._flush_cards)

    def _flush_cards(self):
        batch = self._pending_cards[:CARD_BATCH_SIZE]
        self._pending_cards = self._pending_cards[CARD_BATCH_SIZE:]
        for msg in batch:
            self._add_message_card(msg)
        self._update_counts()
        if self._pending_cards:
            self.after(CARD_FLUSH_DELAY_MS, self._flush_cards)
        else:
            self._card_flush_scheduled = False

    def _add_message_card(self, msg):
        card = ctk.CTkFrame(self.timeline, fg_color=C["card"], corner_radius=10)
        card.pack(fill="x", pady=4, padx=8)

        username = self.logged_in_user["username"] if self.logged_in_user else "You"
        var = ctk.BooleanVar(value=False)
        self.check_vars[msg["id"]] = var
        self.cards[msg["id"]] = card

        def on_toggle():
            if var.get():
                self.selected_ids.add(msg["id"])
            else:
                self.selected_ids.discard(msg["id"])
            self._update_counts()

        avatar = ctk.CTkLabel(card, text="", image=avatar_ctk_image(username, 34))
        avatar.pack(side="left", padx=(10, 8), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, pady=8)

        top = ctk.CTkFrame(info, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=username, text_color=C["text"],
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            top, text=format_discord_timestamp(msg.get("timestamp")),
            text_color=C["text_faint"], font=ctk.CTkFont(family=FONT, size=11),
        ).pack(side="left", padx=8)
        if msg.get("attachments"):
            ctk.CTkLabel(
                top, text="📎 attachment", text_color=C["accent"],
                font=ctk.CTkFont(family=FONT, size=10),
            ).pack(side="left")

        content = msg.get("content", "") or ("[Attachment]" if msg.get("attachments") else "")
        ctk.CTkLabel(
            info, text=content, anchor="w", justify="left", wraplength=600,
            text_color=C["content"], font=ctk.CTkFont(family=FONT, size=12),
        ).pack(fill="x", pady=(1, 0))

        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=10)
        ctk.CTkCheckBox(
            right, text="", width=24, variable=var, command=on_toggle,
            checkbox_width=20, checkbox_height=20, corner_radius=6,
            border_color=C["text_faint"], fg_color=C["primary"], hover_color=C["primary_hover"],
        ).pack(anchor="e")
        ctk.CTkLabel(
            right, text=msg["id"], text_color=C["text_faint"],
            font=ctk.CTkFont(family="Consolas", size=9),
        ).pack(anchor="e")

        # Click anywhere on the card (except the checkbox) toggles selection
        def toggle(_event=None):
            var.set(not var.get())
            on_toggle()

        for widget in (card, info, top, avatar):
            widget.bind("<Button-1>", toggle)

    # --- Scanning ------------------------------------------------------------------------

    def start_scan(self):
        if not self.client or not self.deleter:
            messagebox.showerror("Error", "Please login first")
            return
        if self.is_scanning or self.is_deleting:
            self.log("Already busy — press STOP to cancel the current operation.")
            return

        context_id = self.entry_id.get().strip()
        if not context_id.isdigit() or not (15 <= len(context_id) <= 21):
            self.log("Invalid ID — it should be a long numeric snowflake.")
            return

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

        # Clear previous results
        self.scanned_messages = []
        self._pending_cards = []
        self._card_flush_scheduled = False
        for widget in self.timeline.winfo_children():
            widget.destroy()
        self.selected_ids.clear()
        self.check_vars.clear()
        self.cards.clear()
        self._update_counts()
        self._show_empty_state()

        is_dm = self.target == "dm"
        query = self.entry_filter.get().strip() or None

        def run_scan():
            try:
                msgs, errors = self.deleter.scan_messages(
                    context_id=context_id,
                    is_dm=is_dm,
                    content_query=query,
                    min_id=min_id,
                    max_id=max_id,
                    progress_callback=self._queue_cards,
                    stop_event=self.stop_event,
                )
                self.after(0, lambda: self.on_scan_complete(msgs, errors))
            except AuthenticationError:
                self.after(0, self._on_token_invalid)
            except Exception as exc:
                error_message = str(exc)
                self.after(0, lambda: self.on_scan_error(error_message))

        threading.Thread(target=run_scan, daemon=True).start()

    def on_scan_complete(self, msgs, errors):
        self.stop_loading_ui()
        for error in errors:
            self.log(f"Search reported: {error}")
        if msgs:
            self.log(f"Scan complete — {len(msgs)} messages found. Select the ones to delete.")
        else:
            self.log("No messages found.")

    def on_scan_error(self, message):
        self.stop_loading_ui()
        self.log(f"Scan failed: {message}")

    def _on_token_invalid(self):
        self.stop_loading_ui()
        self.lbl_userstatus.configure(text="Token invalid/expired", text_color=C["danger"])
        self.rail_dot.configure(text_color=C["offline"])
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

    # --- Deletion -----------------------------------------------------------------------------

    def start_delete(self):
        if self.is_scanning or self.is_deleting:
            self.log("Already busy.")
            return
        msgs_to_del = [m for m in self.scanned_messages if m["id"] in self.selected_ids]
        if not msgs_to_del:
            return

        count = len(msgs_to_del)
        if not messagebox.askyesno("Confirm", f"Delete {count} messages?\nThis cannot be undone."):
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
                self.after(0, lambda: self.on_del_complete(result))
            except AuthenticationError:
                self.after(0, self._on_token_invalid)
            except Exception as exc:
                error_message = str(exc)
                self.after(0, lambda: self.on_del_error(error_message))

        threading.Thread(target=run_del, daemon=True).start()

    def update_delete_status(self, deleted, failed, total):
        if total > 0:
            self.bottom_progress.set((deleted + failed) / total)
        self.log(f"Deleting: {deleted}/{total} (failed: {failed})")

    def on_del_complete(self, result):
        self.is_deleting = False
        self._show_progress(False)
        self.btn_stop.configure(state="disabled")
        self.btn_scan.configure(state="normal")

        deleted_ids = set(result["deleted_ids"])
        self.scanned_messages = [m for m in self.scanned_messages if m["id"] not in deleted_ids]
        for msg_id in deleted_ids:
            card = self.cards.pop(msg_id, None)
            if card is not None:
                card.destroy()
            self.check_vars.pop(msg_id, None)
            self.selected_ids.discard(msg_id)
        self._update_counts()
        self.btn_delete.configure(state="disabled", text="DELETE SELECTED")

        summary = f"Deletion finished — deleted: {result['deleted']}, failed: {result['failed']}."
        if result["cancelled"]:
            summary += " (stopped early)"
        self.log(summary)
        messagebox.showinfo("Done", summary)

    def on_del_error(self, message):
        self.is_deleting = False
        self._show_progress(False)
        self.btn_stop.configure(state="disabled")
        self.btn_scan.configure(state="normal")
        self.btn_delete.configure(state="normal", text="DELETE SELECTED")
        self.log(f"Deletion failed: {message}")

    # --- Selection -------------------------------------------------------------------------

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
        if selected > 0 and not self.is_deleting:
            self.btn_delete.configure(state="normal", text=f"DELETE ({selected})")
        else:
            self.btn_delete.configure(state="disabled", text="DELETE SELECTED")

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
