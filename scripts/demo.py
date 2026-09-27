"""Developer utility: launch the GUI with a fake Discord client.

Usage:
    python scripts/demo.py                          # interactive demo window
    python scripts/demo.py --screenshot out.png     # capture and exit

Lets you exercise the full Discord-style browsing flow (server rail, DM list,
channel switching, selection, keyboard flow) without touching a real account.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402  (import applies Windows DPI awareness before Tk init)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


NOW = datetime.now(timezone.utc)

DEMO_GUILDS = [
    {"id": "900000000000000001", "name": "LeoZeLizard's server", "icon": None},
    {"id": "900000000000000002", "name": "Game Night", "icon": None},
    {"id": "900000000000000003", "name": "Memes & Chill", "icon": None},
    {"id": "900000000000000004", "name": "Dev Talk", "icon": None},
    {"id": "900000000000000005", "name": "Photography", "icon": None},
]

DEMO_GUILD_CHANNELS = {
    "900000000000000001": [
        {"id": "810000000000000001", "type": 0, "name": "general", "position": 0},
        {"id": "810000000000000002", "type": 0, "name": "random", "position": 1},
        {"id": "810000000000000003", "type": 0, "name": "media", "position": 2},
    ],
    "900000000000000002": [
        {"id": "820000000000000001", "type": 0, "name": "lfg", "position": 0},
        {"id": "820000000000000002", "type": 0, "name": "results", "position": 1},
    ],
    "900000000000000003": [
        {"id": "830000000000000001", "type": 0, "name": "memes", "position": 0},
        {"id": "830000000000000002", "type": 0, "name": "shitposting", "position": 1},
        {"id": "830000000000000003", "type": 0, "name": "cursed", "position": 2},
    ],
    "900000000000000004": [
        {"id": "840000000000000001", "type": 0, "name": "python", "position": 0},
        {"id": "840000000000000002", "type": 0, "name": "showcase", "position": 1},
    ],
    "900000000000000005": [
        {"id": "850000000000000001", "type": 0, "name": "landscapes", "position": 0},
    ],
}

DEMO_DMS = [
    {"id": "710000000000000001", "type": 1, "name": "LeoZeLizard",
     "recipient": {"id": "600000000000000001", "username": "leozelizard", "avatar": None}},
    {"id": "710000000000000002", "type": 1, "name": "Alice",
     "recipient": {"id": "600000000000000002", "username": "alice_w", "avatar": None}},
    {"id": "710000000000000003", "type": 1, "name": "Bob",
     "recipient": {"id": "600000000000000003", "username": "bob_builds", "avatar": None}},
    {"id": "710000000000000004", "type": 3, "name": "Group (3 members)", "recipient": {}},
]


def demo_messages(channel_id, count=12):
    """A believable mix of messages for any channel, newest first."""
    lines = [
        ("hey, is anyone still using this channel?", 60, False),
        ("testing something, ignore me", 55, False),
        ("game night tonight at 8? bring snacks\nwe're doing pizza and probably Mario Kart after", 40, False),
        ("", 38, True),
        ("never mind, postponed to friday", 36, False),
        ("lol who remembered this server exists", 1500, False),
        ("happy new year everyone!", 9000, False),
        ("does anyone have that meme from last week", 20000, False),
        ("here it is", 20002, True),
        ("legend, thanks", 20004, False),
        ("brb creating a bulk delete tool", 20006, False),
        ("wait, that's illegal… actually it's just against ToS", 20008, False),
    ]
    messages = []
    for index, (content, minutes_ago, has_attachment) in enumerate(lines[:count]):
        messages.append(
            {
                "id": str(100000 + index),
                "channel_id": channel_id,
                "content": content,
                "timestamp": _iso(NOW - timedelta(minutes=minutes_ago)),
                "attachments": [{"id": "1"}] if has_attachment else [],
                "author": {"id": "100000000000000001"},
                "hit": True,
            }
        )
    return messages


class DemoClient:
    """Duck-typed stand-in for DiscordClient with instant fake data."""

    def __init__(self):
        self.user_id = "100000000000000001"

    def validate_token(self):
        return {
            "id": self.user_id,
            "username": "DemoUser",
            "discriminator": "0",
            "global_name": "DemoUser",
        }

    def fetch_guilds(self):
        return [dict(g) for g in DEMO_GUILDS]

    def fetch_dm_channels(self):
        return [dict(d) for d in DEMO_DMS]

    def fetch_guild_channels(self, guild_id):
        return [dict(c) for c in DEMO_GUILD_CHANNELS.get(guild_id, [])]

    def search_messages(self, **_kwargs):
        page = demo_messages(_kwargs.get("channel_id") or "0")
        return {"messages": [[message] for message in page], "total_results": len(page)}

    def delete_message(self, _channel_id, _message_id):
        return "deleted"

    def close(self):
        pass


def build_demo_app():
    app = gui.DiscordToolGUI()
    app.app_settings.update({"delete_delay_min": 0.5, "delete_delay_max": 0.5})
    fake = DemoClient()
    app.client = fake
    app.token = "demo"
    app.logged_in_user = fake.validate_token()
    app.deleter = gui.MessageDeleter(fake, app.app_settings)
    app._refresh_user_panel()

    # Lock auth to demo mode: a real token typed here would silently replace
    # the fake client and point live deletions at a real account.
    app.entry_token.insert(0, "demo-mode-fake-token")
    app.entry_token.configure(state="disabled")
    app.btn_login.configure(state="disabled", text="Demo mode")
    app.btn_auto_token.configure(state="disabled")

    app._load_servers()
    return app


def _capture_window_win32(app):
    """Capture a window's own pixels via PrintWindow (ignores occlusion)."""
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd = user32.GetAncestor(app.winfo_id(), 2)  # GA_ROOT
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_memory = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    gdi32.SelectObject(hdc_memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, hdc_memory, 2):  # PW_RENDERFULLCONTENT
            raise OSError("PrintWindow failed")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_memory, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        return Image.frombuffer("RGBA", (width, height), buffer.raw, "raw", "BGRA", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_memory)
        user32.ReleaseDC(hwnd, hdc_window)


def capture(app, output):
    """PrintWindow capture with a screen-grab fallback."""
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    time.sleep(0.3)
    image = None
    if sys.platform == "win32":
        try:
            image = _capture_window_win32(app)
        except Exception as exc:
            print(f"PrintWindow capture failed ({exc}) — falling back to screen grab")
    if image is None:
        from PIL import ImageGrab

        x, y = app.winfo_rootx(), app.winfo_rooty()
        image = ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()))
    else:
        # GetWindowRect includes ~8px of invisible resize border on each side.
        image = image.crop((8, 0, image.width - 8, image.height - 8))
    image.convert("RGB").save(output)
    print(f"Screenshot saved to {output}")


def main():
    parser = argparse.ArgumentParser(description="Launch the demo GUI")
    parser.add_argument("--screenshot", metavar="PATH", help="capture a screenshot and exit")
    args = parser.parse_args()

    app = build_demo_app()

    if args.screenshot:
        for _ in range(120):
            app.update()
            time.sleep(0.02)

        # Open a DM so the screenshot shows the full flow with messages
        app._select_channel(DEMO_DMS[0]["id"], DEMO_DMS[0]["name"], "dm")
        for _ in range(100):
            app.update()
            time.sleep(0.02)
        app.select_all()
        app._set_status("Scan complete — 12 messages found. Review and delete.")
        for _ in range(10):
            app.update()
            time.sleep(0.02)

        capture(app, args.screenshot)
        app.destroy()
        return

    app.mainloop()


if __name__ == "__main__":
    main()
