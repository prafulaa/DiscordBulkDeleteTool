"""GUI for the Discord Bulk Delete Tool (CustomTkinter)."""

import threading
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

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

THEME_COLORS = {
    "bg_main": "#313338",       # Discord dark background
    "bg_sidebar": "#2b2d31",    # Discord darker sidebar
    "bg_card": "#2b2d31",       # Message card background
    "text_main": "#dbdee1",     # Main text
    "text_muted": "#949ba4",    # Muted text
    "primary": "#5865F2",       # Discord blurple
    "primary_hover": "#4752c4",
    "danger": "#DA373C",        # Discord red
    "danger_hover": "#a1282c",
    "success": "#248046",       # Discord green
    "success_hover": "#1a6334",
    "input_bg": "#1e1f22",      # Darker input
}

CARD_BATCH_SIZE = 40   # message cards rendered per UI tick
CARD_FLUSH_DELAY_MS = 10

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")


class DiscordToolGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"Discord Bulk Delete Tool — v{VERSION}")
        self.geometry("1200x800")
        self.minsize(1000, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State
        self.client = None
        self.deleter = None
        self.token = ""
        self.logged_in_user = None
        self.scanned_messages = []
        self.selected_ids = set()
        self.check_vars = {}       # message id -> BooleanVar
        self.cards = {}            # message id -> card frame
        self.is_scanning = False
        self.is_deleting = False
        self.stop_event = threading.Event()
        self._pending_cards = []
        self._card_flush_scheduled = False

        self._init_sidebar()
        self._init_main_area()

    # --- UI construction -------------------------------------------------

    def _init_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(
            self, width=280, corner_radius=0, fg_color=THEME_COLORS["bg_sidebar"]
        )
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1)  # spacer

        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Discord Tool",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=THEME_COLORS["text_main"],
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 5), sticky="w")

        version_label = ctk.CTkLabel(
            self.sidebar_frame,
            text=f"v{VERSION} • Bulk Delete",
            font=ctk.CTkFont(size=12),
            text_color=THEME_COLORS["text_muted"],
        )
        version_label.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        auth_label = ctk.CTkLabel(
            self.sidebar_frame, text="AUTHENTICATION",
            text_color=THEME_COLORS["text_muted"],
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        auth_label.grid(row=2, column=0, padx=20, pady=(10, 5), sticky="w")

        self.entry_token = ctk.CTkEntry(
            self.sidebar_frame,
            placeholder_text="Paste User Token",
            show="•",
            fg_color=THEME_COLORS["input_bg"],
            border_color="#1e1f22",
            text_color=THEME_COLORS["text_main"],
            height=35,
        )
        self.entry_token.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.entry_token.bind("<Return>", lambda _event: self.login())

        btn_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.btn_login = ctk.CTkButton(
            btn_frame, text="Login", command=self.login,
            fg_color=THEME_COLORS["primary"], hover_color=THEME_COLORS["primary_hover"],
            height=35,
        )
        self.btn_login.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self.btn_auto_token = ctk.CTkButton(
            btn_frame, text="Auto-Find", command=self.auto_find_token,
            fg_color="#7B1FA2", hover_color="#9C27B0", height=35,
        )
        self.btn_auto_token.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        hint_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Auto-Find reads the local Discord app only.",
            font=ctk.CTkFont(size=10),
            text_color=THEME_COLORS["text_muted"],
            wraplength=230,
            justify="left",
        )
        hint_label.grid(row=5, column=0, padx=20, pady=(2, 0), sticky="w")

        self.lbl_status = ctk.CTkFrame(self.sidebar_frame, fg_color="#3b3d42", corner_radius=6)
        self.lbl_status.grid(row=6, column=0, padx=20, pady=20, sticky="new")

        self.status_indicator = ctk.CTkLabel(
            self.lbl_status, text="●", text_color="gray", font=("Arial", 16)
        )
        self.status_indicator.pack(side="left", padx=(10, 5))

        self.status_text = ctk.CTkLabel(
            self.lbl_status, text="Not Logged In", text_color=THEME_COLORS["text_muted"]
        )
        self.status_text.pack(side="left", pady=8)

        footer_label = ctk.CTkLabel(
            self.sidebar_frame, text="Designed for Discord Users",
            text_color=THEME_COLORS["text_muted"], font=("Arial", 10),
        )
        footer_label.grid(row=7, column=0, padx=20, pady=20)

    def _init_main_area(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=THEME_COLORS["bg_main"])
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.main_frame.grid_rowconfigure(3, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # 1. Control bar (two rows: context / filters)
        self.controls_frame = ctk.CTkFrame(
            self.main_frame, fg_color=THEME_COLORS["bg_sidebar"], corner_radius=0
        )
        self.controls_frame.grid(row=0, column=0, sticky="ew")
        self.controls_frame.grid_columnconfigure(3, weight=1)

        self.radio_var = ctk.IntVar(value=1)
        self.radio_dm = ctk.CTkRadioButton(
            self.controls_frame, text="Direct Message", variable=self.radio_var, value=1,
            fg_color=THEME_COLORS["primary"], hover_color=THEME_COLORS["primary_hover"],
        )
        self.radio_dm.grid(row=0, column=0, padx=(20, 5), pady=(15, 5))

        self.radio_server = ctk.CTkRadioButton(
            self.controls_frame, text="Server (Guild)", variable=self.radio_var, value=2,
            fg_color=THEME_COLORS["primary"], hover_color=THEME_COLORS["primary_hover"],
        )
        self.radio_server.grid(row=0, column=1, padx=(5, 10), pady=(15, 5))

        self.entry_id = ctk.CTkEntry(
            self.controls_frame, placeholder_text="Channel / Guild ID", width=220,
            fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22",
        )
        self.entry_id.grid(row=0, column=2, padx=5, pady=(15, 5))

        self.btn_stop = ctk.CTkButton(
            self.controls_frame, text="STOP", command=self.request_stop, width=80,
            fg_color=THEME_COLORS["danger"], hover_color=THEME_COLORS["danger_hover"],
            state="disabled",
        )
        self.btn_stop.grid(row=0, column=4, padx=5, pady=(15, 5))

        self.scan_btn = ctk.CTkButton(
            self.controls_frame, text="SCAN MESSAGES", command=self.start_scan,
            fg_color=THEME_COLORS["success"], hover_color=THEME_COLORS["success_hover"],
            font=ctk.CTkFont(weight="bold"), width=150,
        )
        self.scan_btn.grid(row=0, column=5, padx=20, pady=(15, 5))

        self.entry_filter = ctk.CTkEntry(
            self.controls_frame, placeholder_text="Keyword filter (optional)",
            fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22",
        )
        self.entry_filter.grid(row=1, column=0, columnspan=2, padx=(20, 10), pady=(5, 15), sticky="ew")

        self.entry_after = ctk.CTkEntry(
            self.controls_frame, placeholder_text="After (YYYY-MM-DD)", width=150,
            fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22",
        )
        self.entry_after.grid(row=1, column=2, padx=5, pady=(5, 15))

        self.entry_before = ctk.CTkEntry(
            self.controls_frame, placeholder_text="Before (YYYY-MM-DD)", width=150,
            fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22",
        )
        self.entry_before.grid(row=1, column=4, padx=5, pady=(5, 15))

        date_hint = ctk.CTkLabel(
            self.controls_frame, text="Dates optional",
            font=ctk.CTkFont(size=10), text_color=THEME_COLORS["text_muted"],
        )
        date_hint.grid(row=1, column=5, padx=(0, 20), pady=(5, 15))

        # 2. Stats & selection bar
        self.stats_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=40)
        self.stats_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(15, 5))

        self.lbl_timeline = ctk.CTkLabel(
            self.stats_frame, text="MESSAGES",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME_COLORS["text_muted"],
        )
        self.lbl_timeline.pack(side="left")

        self.lbl_count = ctk.CTkLabel(
            self.stats_frame, text="(0 found)", text_color=THEME_COLORS["text_muted"]
        )
        self.lbl_count.pack(side="left", padx=5)

        self.btn_select_none = ctk.CTkButton(
            self.stats_frame, text="Deselect All", width=90, height=24,
            command=self.select_none, fg_color="#3b3d42", hover_color="#4e5058",
            text_color="white",
        )
        self.btn_select_none.pack(side="right")

        self.btn_select_all = ctk.CTkButton(
            self.stats_frame, text="Select All", width=90, height=24,
            command=self.select_all, fg_color="#3b3d42", hover_color="#4e5058",
            text_color="white",
        )
        self.btn_select_all.pack(side="right", padx=10)

        # 3. Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            self.main_frame, height=4, progress_color=THEME_COLORS["primary"]
        )
        self.progress_bar.grid(row=2, column=0, sticky="ew")
        self.progress_bar.set(0)
        self.progress_bar.grid_remove()

        # 4. Timeline scroll area
        self.timeline_scroll = ctk.CTkScrollableFrame(
            self.main_frame, corner_radius=0, fg_color="transparent", label_text=""
        )
        self.timeline_scroll.grid(row=3, column=0, sticky="nsew", pady=(5, 0))
        self.timeline_scroll.grid_columnconfigure(0, weight=1)

        # 5. Bottom action bar
        self.action_bar = ctk.CTkFrame(
            self.main_frame, height=60, fg_color=THEME_COLORS["bg_sidebar"], corner_radius=0
        )
        self.action_bar.grid(row=4, column=0, sticky="ew")

        self.log_label = ctk.CTkLabel(
            self.action_bar, text="Ready", text_color=THEME_COLORS["text_muted"], anchor="w"
        )
        self.log_label.pack(side="left", padx=20, fill="x", expand=True)

        self.del_btn = ctk.CTkButton(
            self.action_bar, text="DELETE SELECTED", command=self.start_delete,
            state="disabled", fg_color=THEME_COLORS["danger"],
            hover_color=THEME_COLORS["danger_hover"], width=170, height=35,
            font=ctk.CTkFont(weight="bold"),
        )
        self.del_btn.pack(side="right", padx=20, pady=12)

    # --- Small helpers -----------------------------------------------------

    def log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_label.configure(text=f"[{stamp}] {text}")
        print_info(text)

    def request_stop(self):
        if self.is_scanning or self.is_deleting:
            self.stop_event.set()
            self.log("Stopping after the current operation…")

    def _set_busy(self, busy):
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.scan_btn.configure(state="disabled" if busy else "normal")
        self.btn_login.configure(state="disabled" if busy else "normal")

    def _validate_dates(self):
        """Returns (min_id, max_id) snowflakes, or None if a date is invalid."""
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

    # --- Auth ----------------------------------------------------------------

    def login(self):
        if self.is_scanning or self.is_deleting:
            self.log("Wait for the current operation to finish.")
            return
        token = self.entry_token.get().strip()
        if not token:
            self.status_text.configure(text="Token Required", text_color=THEME_COLORS["danger"])
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
        self.btn_login.configure(state="normal", text="Logged In", fg_color=THEME_COLORS["success"])
        self.status_indicator.configure(text="●", text_color=THEME_COLORS["success"])
        self.status_text.configure(
            text=display_username(user), text_color=THEME_COLORS["text_main"]
        )
        self.log(f"Logged in as {display_username(user)}")

    def on_login_fail(self):
        self.btn_login.configure(state="normal", text="Login", fg_color=THEME_COLORS["primary"])
        self.status_indicator.configure(text="●", text_color=THEME_COLORS["danger"])
        self.status_text.configure(text="Invalid Token", text_color=THEME_COLORS["danger"])
        self.log("Authentication failed")

    def auto_find_token(self):
        self.btn_auto_token.configure(state="disabled", text="...")
        self.log("Searching the local Discord app for tokens…")

        def run_search():
            try:
                tokens = find_tokens()
            except Exception as exc:
                tokens = []
                error_message = str(exc)
                self.after(0, lambda: self.log(f"Token search failed: {error_message}"))
            self.after(0, lambda: self.on_tokens_found(tokens))

        threading.Thread(target=run_search, daemon=True).start()

    def on_tokens_found(self, tokens):
        self.btn_auto_token.configure(state="normal", text="Auto-Find")
        if not tokens:
            messagebox.showinfo(
                "No Tokens",
                "No plaintext Discord tokens found in the local Discord app.\n"
                "(Newer Discord versions encrypt it.)\n\n"
                "Please paste your token manually instead.",
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
        selector.geometry("500x350")
        selector.transient(self)
        selector.grab_set()
        selector.configure(fg_color=THEME_COLORS["bg_main"])

        x = self.winfo_x() + (self.winfo_width() - 500) // 2
        y = self.winfo_y() + (self.winfo_height() - 350) // 2
        selector.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            selector, text="Select Account", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=20)

        scroll = ctk.CTkScrollableFrame(selector, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        def select(token, source):
            self._use_found_token(token, source)
            selector.destroy()

        for token, source in tokens:
            card = ctk.CTkFrame(scroll, fg_color=THEME_COLORS["bg_card"])
            card.pack(fill="x", pady=5)

            ctk.CTkLabel(
                card, text=source, font=ctk.CTkFont(weight="bold"), width=120, anchor="w"
            ).pack(side="left", padx=10, pady=10)
            ctk.CTkLabel(
                card, text=token[:16] + "…", text_color=THEME_COLORS["text_muted"]
            ).pack(side="left", padx=5)
            ctk.CTkButton(
                card, text="Select", width=80,
                command=lambda t=token, s=source: select(t, s),
                fg_color=THEME_COLORS["primary"],
            ).pack(side="right", padx=10)

    # --- Timeline rendering (batched to keep the UI responsive) -----------

    def _queue_cards(self, new_msgs):
        """Called from the scan worker thread — hop to the UI thread."""
        self.after(0, lambda: self._schedule_cards(new_msgs))

    def _schedule_cards(self, new_msgs):
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
        self.lbl_count.configure(text=f"({len(self.scanned_messages)} found)")
        if self._pending_cards:
            self.after(CARD_FLUSH_DELAY_MS, self._flush_cards)
        else:
            self._card_flush_scheduled = False

    def _add_message_card(self, msg):
        card = ctk.CTkFrame(self.timeline_scroll, fg_color=THEME_COLORS["bg_card"], corner_radius=6)
        card.pack(fill="x", pady=4, padx=10)

        var = ctk.BooleanVar(value=False)
        self.check_vars[msg["id"]] = var
        self.cards[msg["id"]] = card

        def on_toggle():
            if var.get():
                self.selected_ids.add(msg["id"])
            else:
                self.selected_ids.discard(msg["id"])
            self.update_delete_btn()

        ctk.CTkCheckBox(
            card, text="", width=24, variable=var, command=on_toggle,
            checkbox_width=20, checkbox_height=20, corner_radius=4, border_color="gray",
        ).pack(side="left", padx=(12, 5), pady=12)

        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        top_row = ctk.CTkFrame(info_frame, fg_color="transparent")
        top_row.pack(fill="x")

        username = self.logged_in_user["username"] if self.logged_in_user else "You"
        ctk.CTkLabel(
            top_row, text=username, font=ctk.CTkFont(weight="bold"),
            text_color=THEME_COLORS["text_main"],
        ).pack(side="left")
        ctk.CTkLabel(
            top_row, text=format_discord_timestamp(msg.get("timestamp")),
            font=ctk.CTkFont(size=11), text_color=THEME_COLORS["text_muted"],
        ).pack(side="left", padx=10)
        ctk.CTkLabel(
            top_row, text=f"ID: {msg['id']}",
            font=ctk.CTkFont(family="Consolas", size=10), text_color="#555",
        ).pack(side="right", padx=5)

        content = msg.get("content", "")
        if not content and msg.get("attachments"):
            content = "[Attachment]"
        ctk.CTkLabel(
            info_frame, text=content, anchor="w", justify="left", wraplength=700,
            text_color="#dcddde",
        ).pack(fill="x", pady=(2, 0))

    # --- Scanning -----------------------------------------------------------

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
        self.progress_bar.grid()
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

        # Clear previous results
        self.scanned_messages = []
        self._pending_cards = []
        self._card_flush_scheduled = False
        for widget in self.timeline_scroll.winfo_children():
            widget.destroy()
        self.selected_ids.clear()
        self.check_vars.clear()
        self.cards.clear()
        self.update_delete_btn()

        is_dm = self.radio_var.get() == 1
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

    def on_scan_error(self, exc):
        self.stop_loading_ui()
        self.log(f"Scan failed: {exc}")

    def _on_token_invalid(self):
        self.stop_loading_ui()
        self.status_indicator.configure(text="●", text_color=THEME_COLORS["danger"])
        self.status_text.configure(text="Token invalid/expired", text_color=THEME_COLORS["danger"])
        self.btn_login.configure(text="Login", fg_color=THEME_COLORS["primary"], state="normal")
        self.log("Your token stopped working — paste a fresh one and log in again.")

    def stop_loading_ui(self):
        self.is_scanning = False
        self.scan_btn.configure(state="normal", text="SCAN MESSAGES")
        self.btn_login.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.progress_bar.stop()
        self.progress_bar.grid_remove()

    # --- Deletion -------------------------------------------------------------

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
        self.del_btn.configure(state="disabled", text="Deleting…")
        self.scan_btn.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.grid()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0)

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
            self.progress_bar.set((deleted + failed) / total)
        self.log(f"Deleting: {deleted}/{total} (failed: {failed})")

    def on_del_complete(self, result):
        self.is_deleting = False
        self.progress_bar.grid_remove()
        self.btn_stop.configure(state="disabled")
        self.scan_btn.configure(state="normal")

        # Remove cards of messages that are actually gone; failures stay
        # in the timeline (still selected) so they can be retried.
        deleted_ids = set(result["deleted_ids"])
        self.scanned_messages = [
            m for m in self.scanned_messages if m["id"] not in deleted_ids
        ]
        for msg_id in deleted_ids:
            card = self.cards.pop(msg_id, None)
            if card is not None:
                card.destroy()
            self.check_vars.pop(msg_id, None)
            self.selected_ids.discard(msg_id)
        self.lbl_count.configure(text=f"({len(self.scanned_messages)} found)")
        self.update_delete_btn()

        summary = f"Deletion finished — deleted: {result['deleted']}, failed: {result['failed']}."
        if result["cancelled"]:
            summary += " (stopped early)"
        self.log(summary)
        messagebox.showinfo("Done", summary)

    def on_del_error(self, exc):
        self.is_deleting = False
        self.progress_bar.grid_remove()
        self.btn_stop.configure(state="disabled")
        self.scan_btn.configure(state="normal")
        self.del_btn.configure(state="normal", text="DELETE SELECTED")
        self.log(f"Deletion failed: {exc}")

    # --- Selection ---------------------------------------------------------

    def select_all(self):
        # Select everything that was scanned, including cards not yet rendered.
        for msg_id in self.check_vars:
            self.check_vars[msg_id].set(True)
        self.selected_ids = {m["id"] for m in self.scanned_messages}
        self.update_delete_btn()

    def select_none(self):
        for var in self.check_vars.values():
            var.set(False)
        self.selected_ids.clear()
        self.update_delete_btn()

    def update_delete_btn(self):
        count = len(self.selected_ids)
        if count > 0 and not self.is_deleting:
            self.del_btn.configure(state="normal", text=f"DELETE ({count})")
        else:
            self.del_btn.configure(state="disabled", text="DELETE SELECTED")

    # --- Lifecycle -----------------------------------------------------------

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
