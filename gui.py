import customtkinter as ctk
import threading
import time
from datetime import datetime
from tkinter import messagebox
from api_client import DiscordClient
from deleter import MessageDeleter
from utils import parse_date

# Set theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")  # Themes: "blue" (standard), "green", "dark-blue"

class DiscordToolGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window setup
        self.title("Discord Bulk Delete Tool")
        self.geometry("1100x700")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State
        self.client = None
        self.deleter = None
        self.token = ""
        self.scanned_messages = []
        self.is_scanning = False
        self.is_scanning = False
        self.is_deleting = False
        self.selected_ids = set()
        self.check_vars = {} # map id -> BooleanVar

        # --- Sidebar ---
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="Discord Tool", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.mode_label = ctk.CTkLabel(self.sidebar_frame, text="Connection", anchor="w")
        self.mode_label.grid(row=1, column=0, padx=20, pady=(10, 0))

        self.entry_token = ctk.CTkEntry(self.sidebar_frame, placeholder_text="Enter User Token", show="*")
        self.entry_token.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.btn_login = ctk.CTkButton(self.sidebar_frame, text="Login & Validate", command=self.login)
        self.btn_login.grid(row=3, column=0, padx=20, pady=10)

        self.lbl_status = ctk.CTkLabel(self.sidebar_frame, text="Status: Not Logged In", text_color="gray")
        self.lbl_status.grid(row=5, column=0, padx=20, pady=10)

        # --- Main Area ---
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_rowconfigure(2, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # 1. Controls
        self.controls_frame = ctk.CTkFrame(self.main_frame)
        self.controls_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        self.controls_frame.grid_columnconfigure(4, weight=1)

        self.radio_var = ctk.IntVar(value=1)
        self.radio_dm = ctk.CTkRadioButton(self.controls_frame, text="DM Channel", variable=self.radio_var, value=1)
        self.radio_dm.grid(row=0, column=0, padx=20, pady=15)
        self.radio_server = ctk.CTkRadioButton(self.controls_frame, text="Server (Guild)", variable=self.radio_var, value=2)
        self.radio_server.grid(row=0, column=1, padx=20, pady=15)

        self.entry_id = ctk.CTkEntry(self.controls_frame, placeholder_text="Channel/Guild ID", width=150)
        self.entry_id.grid(row=0, column=2, padx=10, pady=15)

        self.entry_filter = ctk.CTkEntry(self.controls_frame, placeholder_text="Keyword Filter (Optional)", width=150)
        self.entry_filter.grid(row=0, column=3, padx=10, pady=15)

        self.scan_btn = ctk.CTkButton(self.controls_frame, text="SCAN", command=self.start_scan, fg_color="#E91E63", hover_color="#C2185B")
        self.scan_btn.grid(row=0, column=5, padx=10, pady=15)
        
        self.del_btn = ctk.CTkButton(self.controls_frame, text="DELETE ALL", command=self.start_delete, state="disabled", fg_color="#D32F2F", hover_color="#B71C1C")
        self.del_btn.grid(row=0, column=6, padx=(5, 20), pady=15)

        # 1.5 Progress Bar (Hidden by default)
        self.progress_bar = ctk.CTkProgressBar(self.main_frame, height=15)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.progress_bar.set(0)
        self.progress_bar.grid_remove() # Hide initially

        # 2. Timeline Header
        self.header_frame = ctk.CTkFrame(self.main_frame, height=40)
        self.header_frame.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        self.lbl_timeline = ctk.CTkLabel(self.header_frame, text="Message Timeline (0 found)", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_timeline = ctk.CTkLabel(self.header_frame, text="Message Timeline (0 found)", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_timeline.pack(side="left", padx=20, pady=5)
        
        self.btn_select_all = ctk.CTkButton(self.header_frame, text="Select All", width=80, height=24, command=self.select_all)
        self.btn_select_all.pack(side="right", padx=10)
        
        self.btn_select_none = ctk.CTkButton(self.header_frame, text="Select None", width=80, height=24, command=self.select_none)
        self.btn_select_none.pack(side="right", padx=0)

        # 3. Timeline (Scrollable)
        self.timeline_scroll = ctk.CTkScrollableFrame(self.main_frame, label_text="Messages")
        self.timeline_scroll.grid(row=3, column=0, sticky="nsew")
        self.timeline_scroll.grid_columnconfigure(0, weight=1)

        # 4. Progress / Logs
        self.log_frame = ctk.CTkTextbox(self.main_frame, height=100)
        self.log_frame.grid(row=4, column=0, sticky="ew", pady=(20, 0))
        self.log_frame.insert("0.0", "Ready.\n")

    def log(self, text):
        self.log_frame.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
        self.log_frame.see("end")

    def login(self):
        token = self.entry_token.get().strip()
        if not token:
            self.log("Error: Token cannot be empty.")
            return
        
        self.btn_login.configure(state="disabled", text="Verifying...")
        
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
        self.btn_login.configure(state="normal", text="Login & Validate", fg_color="green")
        self.lbl_status.configure(text=f"Logged in as: {user['username']}", text_color="#4CAF50")
        self.log(f"Successfully logged in as {user['username']}#{user['discriminator']}")
        self.deleter = MessageDeleter(self.client)

    def on_login_fail(self):
        self.btn_login.configure(state="normal", text="Login & Validate", fg_color="#1f6aa5")
        self.lbl_status.configure(text="Status: Authentication Failed", text_color="#F44336")
        self.log("Error: Authentication failed. Check your token.")

    def add_message_card(self, msg):
        # Create a "card" for the message
        card = ctk.CTkFrame(self.timeline_scroll, fg_color=("#2b2b2b", "#2b2b2b")) # Dark gray
        card = ctk.CTkFrame(self.timeline_scroll, fg_color=("#2b2b2b", "#2b2b2b")) # Dark gray
        card.pack(fill="x", pady=2, padx=5)

        # Checkbox
        var = ctk.BooleanVar(value=False)
        self.check_vars[msg['id']] = var
        
        def on_toggle():
            if var.get():
                self.selected_ids.add(msg['id'])
            else:
                self.selected_ids.discard(msg['id'])
            self.update_delete_button_text()

        chk = ctk.CTkCheckBox(card, text="", width=24, variable=var, command=on_toggle)
        chk.pack(side="left", padx=(10, 0))
        
        # Timestamp
        color = "#999999"
        ts_lbl = ctk.CTkLabel(card, text=msg['timestamp'], width=150, anchor="w", text_color=color, font=("Consolas", 11))
        ts_lbl.pack(side="left", padx=10)
        
        # ID
        id_lbl = ctk.CTkLabel(card, text=msg['id'], width=150, anchor="w", text_color="#555555", font=("Consolas", 10))
        id_lbl.pack(side="left", padx=5)

        # Content - truncate if long
        content_text = msg['content'].replace("\n", " ")
        if len(content_text) > 80: content_text = content_text[:80] + "..."
        
        content_lbl = ctk.CTkLabel(card, text=content_text, anchor="w", justify="left")
        content_lbl.pack(side="left", fill="x", expand=True, padx=10)

    def start_scan(self):
        if not self.client:
            self.log("Please login first.")
            return
        
        ctx_id = self.entry_id.get().strip()
        if not ctx_id.isdigit():
            self.log("Error: Invalid ID (must be numeric).")
            return
            
        self.is_scanning = True
        self.scan_btn.configure(state="disabled", text="Scanning...")
        self.del_btn.configure(state="disabled")
        self.scanned_messages = []
        
        # Clear existing ui
        for widget in self.timeline_scroll.winfo_children():
            widget.destroy()

        self.log(f"Started scan for ID: {ctx_id}")
        self.selected_ids.clear()
        self.check_vars.clear()
        self.update_delete_button_text()
        
        is_dm = (self.radio_var.get() == 1)
        query = self.entry_filter.get().strip() or None
        
        def run_scan():
            try:
                # We won't retrieve ALL at once if we want dynamic updates,
                # but our deleter returns all at the end. 
                # However, we added a progress_callback!
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
                self.after(0, lambda: self.log(f"Scan error: {e}"))
                self.after(0, lambda: self.scan_btn.configure(state="normal", text="SCAN"))

        threading.Thread(target=run_scan, daemon=True).start()

    def update_timeline(self, new_msgs):
        self.scanned_messages.extend(new_msgs)
        self.lbl_timeline.configure(text=f"Message Timeline ({len(self.scanned_messages)} found)")
        for msg in new_msgs:
            self.add_message_card(msg)
        # Scroll to bottom
        # self.timeline_scroll._parent_canvas.yview_moveto(1.0) 

    def on_scan_complete(self, msgs):
        self.is_scanning = False
        self.scan_btn.configure(state="normal", text="SCAN")
        
        if msgs:
            self.del_btn.configure(state="normal")
            self.log(f"Scan complete. Found {len(msgs)} messages.")
        else:
            self.log("Scan complete. No messages found.")

    def start_delete(self):
        if not self.scanned_messages:
            return

        # Filter for selected
        msgs_to_delete = [m for m in self.scanned_messages if m['id'] in self.selected_ids]
        
        if not msgs_to_delete:
            messagebox.showwarning("No Selection", "Please select at least one message to delete.")
            return
            
        count = len(msgs_to_delete)
        
        # GUI Confirmation
        if not messagebox.askyesno("Confirm Deletion", f"Are you SURE you want to delete {count} messages?\nThis cannot be undone."):
            self.log("Deletion cancelled by user.")
            return

        self.del_btn.configure(state="disabled", text="Deleting...")
        self.scan_btn.configure(state="disabled")
        
        # Show progress bar
        self.progress_bar.grid()
        self.progress_bar.set(0)
        
        self.log(f"Starting deletion of {count} messages...")
        
        def run_delete():
            def cb(deleted, failed, total):
                 self.after(0, lambda: self.update_status(deleted, failed, total))

            # Pass skip_confirm=True because we already asked in GUI
            self.deleter.execute_deletion(msgs_to_delete, progress_callback=cb, skip_confirm=True)
            self.after(0, self.on_delete_complete)

        threading.Thread(target=run_delete, daemon=True).start()

    def update_status(self, deleted, failed, total):
        self.lbl_status.configure(text=f"Deleting... {deleted}/{total} (Failed: {failed})", text_color="orange")
        
        # Update progress bar
        if total > 0:
            progress = deleted / total
            self.progress_bar.set(progress)
            
        # Optional: Log every 10 items or so to keep log alive but not spammy
        if deleted % 10 == 0:
             self.lbl_timeline.configure(text=f"Deleting... {deleted}/{total}")

    def on_delete_complete(self):
        self.log("Deletion process finished.")
        self.lbl_status.configure(text="Ready", text_color="gray")
        self.del_btn.configure(state="disabled", text="DELETE ALL")
        self.scan_btn.configure(state="normal")
        self.progress_bar.grid_remove() # Hide progress bar
        
        # Clear list
        self.scanned_messages = []
        # Clear UI
        for widget in self.timeline_scroll.winfo_children():
            widget.destroy()

    def select_all(self):
        for mid, var in self.check_vars.items():
            var.set(True)
            self.selected_ids.add(mid)
        self.update_delete_button_text()

    def select_none(self):
        for mid, var in self.check_vars.items():
            var.set(False)
            self.selected_ids.clear()
        self.update_delete_button_text()
        
    def update_delete_button_text(self):
        count = len(self.selected_ids)
        if count > 0:
            self.del_btn.configure(text=f"DELETE ({count})")
        else:
            self.del_btn.configure(text="DELETE ALL" if not self.scanned_messages else "DELETE (0)")


if __name__ == "__main__":
    app = DiscordToolGUI()
    app.mainloop()
