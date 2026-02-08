import customtkinter as ctk
import threading
import time
from datetime import datetime
from tkinter import messagebox
from api_client import DiscordClient
from deleter import MessageDeleter
from utils import parse_date
from token_finder import find_tokens, validate_token_format
import webbrowser

# --- CONSTANTS & THEME ---
THEME_COLORS = {
    "bg_main": "#313338",       # Discord Dark Background
    "bg_sidebar": "#2b2d31",    # Discord Darker Sidebar
    "bg_card": "#2b2d31",       # Message Card Background
    "text_main": "#dbdee1",     # Main Text
    "text_muted": "#949ba4",    # Muted Text
    "primary": "#5865F2",       # Discord Blurple
    "primary_hover": "#4752c4",
    "danger": "#DA373C",        # Discord Red
    "danger_hover": "#a1282c",
    "success": "#248046",       # Discord Green
    "success_hover": "#1a6334",
    "input_bg": "#1e1f22",      # Darker Input
    "scroll_bg": "#2b2d31"      # Scroll container
}

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

class DiscordToolGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("Discord Bulk Delete Tool")
        self.geometry("1200x800")
        self.minsize(1000, 700)
        
        # Configure Grid
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State Variables
        self.client = None
        self.deleter = None
        self.token = ""
        self.scanned_messages = []
        self.is_scanning = False
        self.selected_ids = set()
        self.check_vars = {} 
        self.logged_in_user = None  # Store logged in user info 

        # --- INIT UI COMPONENTS ---
        self._init_sidebar()
        self._init_main_area()

    def _init_sidebar(self):
        """Initialize the Sidebar (Left Panel)"""
        self.sidebar_frame = ctk.CTkFrame(self, width=280, corner_radius=0, fg_color=THEME_COLORS["bg_sidebar"])
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1) # Spacer push down

        # 1. Logo / Title
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="Discord Tool", 
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=THEME_COLORS["text_main"]
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 10), sticky="w")
        
        version_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="v2.0 • Bulk Delete", 
            font=ctk.CTkFont(size=12),
            text_color=THEME_COLORS["text_muted"]
        )
        version_label.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        # 2. Authentication Section
        auth_label = ctk.CTkLabel(self.sidebar_frame, text="AUTHENTICATION", text_color=THEME_COLORS["text_muted"], font=ctk.CTkFont(size=11, weight="bold"))
        auth_label.grid(row=2, column=0, padx=20, pady=(10, 5), sticky="w")

        self.entry_token = ctk.CTkEntry(
            self.sidebar_frame, 
            placeholder_text="Paste User Token", 
            show="*",
            fg_color=THEME_COLORS["input_bg"],
            border_color="#1e1f22",
            text_color=THEME_COLORS["text_main"],
            height=35
        )
        self.entry_token.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Buttons Frame for Login/Auto
        btn_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.btn_login = ctk.CTkButton(
            btn_frame, 
            text="Login", 
            command=self.login,
            fg_color=THEME_COLORS["primary"],
            hover_color=THEME_COLORS["primary_hover"],
            height=35
        )
        self.btn_login.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self.btn_auto_token = ctk.CTkButton(
            btn_frame, 
            text="Auto-Find", 
            command=self.auto_find_token,
            fg_color="#7B1FA2",  # Custom Purple for functionality distinction
            hover_color="#9C27B0",
            height=35
        )
        self.btn_auto_token.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # Status
        self.lbl_status = ctk.CTkFrame(self.sidebar_frame, fg_color="#3b3d42", corner_radius=6)
        self.lbl_status.grid(row=5, column=0, padx=20, pady=20, sticky="ew")
        
        self.status_indicator = ctk.CTkLabel(self.lbl_status, text="●", text_color="gray", font=("Arial", 16))
        self.status_indicator.pack(side="left", padx=(10, 5))
        
        self.status_text = ctk.CTkLabel(self.lbl_status, text="Not Logged In", text_color=THEME_COLORS["text_muted"])
        self.status_text.pack(side="left", pady=8)

        # 3. Footer / About
        footer_label = ctk.CTkLabel(self.sidebar_frame, text="Designed for Discord Users", text_color=THEME_COLORS["text_muted"], font=("Arial", 10))
        footer_label.grid(row=7, column=0, padx=20, pady=20)

    def _init_main_area(self):
        """Initialize the Main Content Area"""
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=THEME_COLORS["bg_main"])
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.main_frame.grid_rowconfigure(3, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # 1. Top Control Bar
        self.controls_frame = ctk.CTkFrame(self.main_frame, fg_color=THEME_COLORS["bg_sidebar"], height=70, corner_radius=0)
        self.controls_frame.grid(row=0, column=0, sticky="ew")
        self.controls_frame.grid_columnconfigure(5, weight=1) # Push scan btn right

        # Radio Buttons custom style
        self.radio_var = ctk.IntVar(value=1)
        self.radio_dm = ctk.CTkRadioButton(self.controls_frame, text="Direct Message", variable=self.radio_var, value=1, fg_color=THEME_COLORS["primary"], hover_color=THEME_COLORS["primary_hover"])
        self.radio_dm.grid(row=0, column=0, padx=20, pady=20)
        
        self.radio_server = ctk.CTkRadioButton(self.controls_frame, text="Server (Guild)", variable=self.radio_var, value=2, fg_color=THEME_COLORS["primary"], hover_color=THEME_COLORS["primary_hover"])
        self.radio_server.grid(row=0, column=1, padx=(0, 20), pady=0)

        # Inputs
        self.entry_id = ctk.CTkEntry(self.controls_frame, placeholder_text="Channel / Guild ID", width=200, fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22")
        self.entry_id.grid(row=0, column=2, padx=5, pady=0)

        self.entry_filter = ctk.CTkEntry(self.controls_frame, placeholder_text="Keyword Filter (Optional)", width=200, fg_color=THEME_COLORS["input_bg"], border_color="#1e1f22")
        self.entry_filter.grid(row=0, column=3, padx=10, pady=0)

        self.scan_btn = ctk.CTkButton(
            self.controls_frame, 
            text="SCAN MESSAGES", 
            command=self.start_scan,
            fg_color=THEME_COLORS["success"], 
            hover_color=THEME_COLORS["success_hover"],
            font=ctk.CTkFont(weight="bold")
        )
        self.scan_btn.grid(row=0, column=6, padx=20, pady=0)

        # 2. Stats & Selection Bar
        self.stats_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=40)
        self.stats_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(15, 5))
        
        self.lbl_timeline = ctk.CTkLabel(self.stats_frame, text="MESSAGES", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME_COLORS["text_muted"])
        self.lbl_timeline.pack(side="left")
        
        self.lbl_count = ctk.CTkLabel(self.stats_frame, text="(0 found)", text_color=THEME_COLORS["text_muted"], padx=5)
        self.lbl_count.pack(side="left")

        self.btn_select_none = ctk.CTkButton(self.stats_frame, text="Deselect All", width=80, height=24, command=self.select_none, fg_color="#3b3d42", hover_color="#4e5058", text_color="white")
        self.btn_select_none.pack(side="right")
        
        self.btn_select_all = ctk.CTkButton(self.stats_frame, text="Select All", width=80, height=24, command=self.select_all, fg_color="#3b3d42", hover_color="#4e5058", text_color="white")
        self.btn_select_all.pack(side="right", padx=10)

        # 3. Progress Bar (Modern)
        self.progress_bar = ctk.CTkProgressBar(self.main_frame, height=4, progress_color=THEME_COLORS["primary"])
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=0, pady=0)
        self.progress_bar.set(0)
        self.progress_bar.grid_remove()

        # 4. Timeline Scroll Area
        self.timeline_scroll = ctk.CTkScrollableFrame(self.main_frame, corner_radius=0, fg_color="transparent", label_text="")
        self.timeline_scroll.grid(row=3, column=0, sticky="nsew", padx=0, pady=(5, 0))
        self.timeline_scroll.grid_columnconfigure(0, weight=1)

        # 5. Bottom Action Bar
        self.action_bar = ctk.CTkFrame(self.main_frame, height=60, fg_color=THEME_COLORS["bg_sidebar"], corner_radius=0)
        self.action_bar.grid(row=4, column=0, sticky="ew")
        
        self.log_label = ctk.CTkLabel(self.action_bar, text="Ready", text_color=THEME_COLORS["text_muted"], anchor="w")
        self.log_label.pack(side="left", padx=20, fill="x", expand=True)
        
        self.del_btn = ctk.CTkButton(
            self.action_bar, 
            text="DELETE SELECTED", 
            command=self.start_delete, 
            state="disabled", 
            fg_color=THEME_COLORS["danger"], 
            hover_color=THEME_COLORS["danger_hover"],
            width=150,
            height=35,
            font=ctk.CTkFont(weight="bold")
        )
        self.del_btn.pack(side="right", padx=20, pady=12)

    # --- LOGIC METHODS ---

    def log(self, text):
        # Update status bar instead of text box
        time_str = datetime.now().strftime('%H:%M:%S')
        self.log_label.configure(text=f"[{time_str}] {text}")
        print(f"[{time_str}] {text}") # Keep printing to console for debug

    def login(self):
        token = self.entry_token.get().strip()
        if not token:
            self.status_text.configure(text="Token Required", text_color=THEME_COLORS["danger"])
            return
        
        self.btn_login.configure(state="disabled", text="...")
        
        def run_auth():
            client = DiscordClient(token)
            user = client.validate_token()
            if user:
                self.client = client
                self.token = token
                self.after(0, lambda: self.on_login_success(user))
            else:
                self.after(0, self.on_login_fail)

        threading.Thread(target=run_auth, daemon=True).start()

    def on_login_success(self, user):
        self.btn_login.configure(state="normal", text="Logged In", fg_color=THEME_COLORS["success"])
        self.status_indicator.configure(text="●", text_color=THEME_COLORS["success"])
        self.status_text.configure(text=f"{user['username']}#{user['discriminator']}", text_color=THEME_COLORS["text_main"])
        self.logged_in_user = user  # Store user info
        self.deleter = MessageDeleter(self.client)
        self.log(f"Logged in as {user['username']}")

    def on_login_fail(self):
        self.btn_login.configure(state="normal", text="Login")
        self.status_indicator.configure(text="●", text_color=THEME_COLORS["danger"])
        self.status_text.configure(text="Invalid Token", text_color=THEME_COLORS["danger"])
        self.log("Authentication failed")

    def auto_find_token(self):
        self.btn_auto_token.configure(state="disabled", text="...")
        self.log("Searching for tokens...")
        
        def run_search():
            try:
                tokens = find_tokens()
                self.after(0, lambda: self.on_tokens_found(tokens))
            except Exception as e:
                self.after(0, lambda: self.log(f"Error: {e}"))
                self.after(0, lambda: self.btn_auto_token.configure(state="normal", text="Auto-Find"))

        threading.Thread(target=run_search, daemon=True).start()

    def on_tokens_found(self, tokens):
        self.btn_auto_token.configure(state="normal", text="Auto-Find")
        
        if not tokens:
            messagebox.showinfo("No Tokens", "No Discord tokens found.\nMake sure you are logged into Discord app or browser.")
            return

        if len(tokens) == 1:
            token, source = tokens[0]
            self.entry_token.delete(0, 'end')
            self.entry_token.insert(0, token)
            self.log(f"Found token from {source}")
        else:
            self.show_token_selector(tokens)

    def show_token_selector(self, tokens):
        selector = ctk.CTkToplevel(self)
        selector.title("Select Token")
        selector.geometry("500x350")
        selector.transient(self)
        selector.grab_set()
        selector.configure(fg_color=THEME_COLORS["bg_main"])
        
        # Center
        x = self.winfo_x() + (self.winfo_width() - 500) // 2
        y = self.winfo_y() + (self.winfo_height() - 350) // 2
        selector.geometry(f"+{x}+{y}")

        ctk.CTkLabel(selector, text="Select Account", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)

        scroll = ctk.CTkScrollableFrame(selector, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        def select(t, s):
            self.entry_token.delete(0, 'end')
            self.entry_token.insert(0, t)
            self.log(f"Selected token from {s}")
            selector.destroy()

        for token, source in tokens:
            card = ctk.CTkFrame(scroll, fg_color=THEME_COLORS["bg_card"])
            card.pack(fill="x", pady=5)
            
            ctk.CTkLabel(card, text=source, font=ctk.CTkFont(weight="bold"), width=100, anchor="w").pack(side="left", padx=10, pady=10)
            ctk.CTkLabel(card, text=token[:20]+"...", text_color=THEME_COLORS["text_muted"]).pack(side="left", padx=5)
            ctk.CTkButton(card, text="Select", width=80, command=lambda t=token, s=source: select(t, s), fg_color=THEME_COLORS["primary"]).pack(side="right", padx=10)

    def add_message_card(self, msg):
        # Modern Message Card
        card = ctk.CTkFrame(self.timeline_scroll, fg_color=THEME_COLORS["bg_card"], corner_radius=6)
        card.pack(fill="x", pady=4, padx=10)

        # Left: Checkbox
        var = ctk.BooleanVar(value=False)
        self.check_vars[msg['id']] = var
        
        def on_toggle():
            if var.get(): self.selected_ids.add(msg['id'])
            else: self.selected_ids.discard(msg['id'])
            self.update_delete_btn()

        chk = ctk.CTkCheckBox(card, text="", width=24, variable=var, command=on_toggle, checkbox_width=20, checkbox_height=20, corner_radius=4, border_color="gray")
        chk.pack(side="left", padx=(12, 5), pady=12)

        # Info Container
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        # Top Row: Author + Timestamp
        top_row = ctk.CTkFrame(info_frame, fg_color="transparent")
        top_row.pack(fill="x")
        
        tstamp = msg.get('timestamp', 'Unknown Date')
        try:
             # Try clean up timestamp
             dt = datetime.strptime(tstamp, "%Y-%m-%dT%H:%M:%S.%f%z")
             tstamp = dt.strftime("%B %d, %Y at %I:%M %p")
        except: pass

        # Get username from logged in user (since all messages are from token owner)
        username = self.logged_in_user['username'] if self.logged_in_user else 'You'
        ctk.CTkLabel(top_row, text=username, font=ctk.CTkFont(weight="bold"), text_color=THEME_COLORS["text_main"]).pack(side="left")
        ctk.CTkLabel(top_row, text=tstamp, font=ctk.CTkFont(size=11), text_color=THEME_COLORS["text_muted"]).pack(side="left", padx=10)
        ctk.CTkLabel(top_row, text=f"ID: {msg['id']}", font=ctk.CTkFont(family="Consolas", size=10), text_color="#555").pack(side="right", padx=5)

        # Content
        content = msg.get('content', '')
        if not content and msg.get('attachments'):
            content = "[Attachment]"
        
        ctk.CTkLabel(info_frame, text=content, anchor="w", justify="left", wraplength=700, text_color="#dcddde").pack(fill="x", pady=(2, 0))

    def start_scan(self):
        if not self.client:
            messagebox.showerror("Error", "Please login first")
            return
        
        ctx_id = self.entry_id.get().strip()
        if not ctx_id.isdigit():
            self.log("Invalid ID")
            return

        self.is_scanning = True
        self.scan_btn.configure(state="disabled", text="Scanning...")
        self.progress_bar.grid()
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()
        
        # Clear
        self.scanned_messages = []
        for w in self.timeline_scroll.winfo_children(): w.destroy()
        self.selected_ids.clear()
        self.check_vars.clear()
        self.update_delete_btn()
        
        is_dm = (self.radio_var.get() == 1)
        query = self.entry_filter.get().strip() or None
        
        def run_scan():
            try:
                def cb(new_msgs):
                     self.after(0, lambda: self.update_timeline(new_msgs))
                
                msgs = self.deleter.scan_messages(
                    context_id=ctx_id, 
                    is_dm=is_dm, 
                    content_query=query, 
                    progress_callback=cb
                )
                self.after(0, lambda: self.on_scan_complete(msgs))
            except Exception as e:
                self.after(0, lambda: self.log(f"Scan failed: {e}"))
                self.after(0, self.stop_loading_ui)

        threading.Thread(target=run_scan, daemon=True).start()

    def update_timeline(self, new_msgs):
        self.scanned_messages.extend(new_msgs)
        self.lbl_count.configure(text=f"({len(self.scanned_messages)} found)")
        for msg in new_msgs:
            self.add_message_card(msg)
    
    def on_scan_complete(self, msgs):
        self.stop_loading_ui()
        if msgs:
            self.del_btn.configure(state="normal")
            self.log(f"Scan complete. Found {len(msgs)} messages.")
        else:
            self.log("No messages found.")

    def stop_loading_ui(self):
        self.is_scanning = False
        self.scan_btn.configure(state="normal", text="SCAN MESSAGES")
        self.progress_bar.stop()
        self.progress_bar.grid_remove()

    def start_delete(self):
        msgs_to_del = [m for m in self.scanned_messages if m['id'] in self.selected_ids]
        if not msgs_to_del: return

        count = len(msgs_to_del)
        if not messagebox.askyesno("Confirm", f"Delete {count} messages?\nCannot be undone."): return

        self.del_btn.configure(state="disabled", text="Deleting...")
        self.progress_bar.grid()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0)
        
        def run_del():
            def cb(deleted, failed, total):
                self.after(0, lambda: self.update_status(deleted, failed, total))
            
            self.deleter.execute_deletion(msgs_to_del, dry_run=False, progress_callback=cb, skip_confirm=True)
            self.after(0, self.on_del_complete)

        threading.Thread(target=run_del, daemon=True).start()

    def update_status(self, d, f, t):
        if t > 0: self.progress_bar.set(d/t)
        self.log(f"Deleting: {d}/{t} (Failed: {f})")

    def on_del_complete(self):
        self.log("Deletion complete")
        self.del_btn.configure(state="disabled", text="DELETE SELECTED")
        self.progress_bar.grid_remove()
        
        # Remove deleted from UI
        # Basic refresh: clear all and re-add remaining? Or just clear checked.
        # For now, just clearing selection
        self.selected_ids.clear()
        for var in self.check_vars.values(): var.set(False)
        self.update_delete_btn()
        
        messagebox.showinfo("Done", "Deletion process finished.")

    def select_all(self):
        for mid, var in self.check_vars.items():
            var.set(True)
            self.selected_ids.add(mid)
        self.update_delete_btn()

    def select_none(self):
        for var in self.check_vars.values(): var.set(False)
        self.selected_ids.clear()
        self.update_delete_btn()

    def update_delete_btn(self):
        c = len(self.selected_ids)
        if c > 0:
            self.del_btn.configure(state="normal", text=f"DELETE ({c})")
        else:
            self.del_btn.configure(state="disabled", text="DELETE SELECTED")

if __name__ == "__main__":
    app = DiscordToolGUI()
    app.mainloop()
