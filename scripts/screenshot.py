"""Developer utility: boot the GUI with demo data and capture a screenshot.

Usage:  python scripts/screenshot.py [output.png]
Used to regenerate docs/screenshot.png for the README.

On Windows the window is captured via PrintWindow, so the capture works even
when other windows are on top; other platforms fall back to a screen grab of
the window's bounding box.
"""

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402  (import applies Windows DPI awareness before Tk init)

DEMO_MESSAGES = [
    ("hey, is anyone still using this channel?", "2025-03-26T21:12:00+00:00", False),
    ("testing something, ignore me", "2025-03-26T21:14:00+00:00", False),
    ("game night tonight at 8? bring snacks", "2025-03-26T21:30:00+00:00", False),
    ("", "2025-03-26T21:31:00+00:00", True),
    ("never mind, postponed to friday", "2025-03-26T21:33:00+00:00", False),
    ("lol who remembered this server exists", "2025-02-14T18:02:00+00:00", False),
    ("happy new year everyone!", "2025-01-01T00:03:00+00:00", False),
    ("does anyone have that meme from last week", "2024-12-28T16:45:00+00:00", False),
    ("here it is", "2024-12-28T16:47:00+00:00", True),
    ("legend, thanks", "2024-12-28T16:49:00+00:00", False),
    ("brb creating a bulk delete tool", "2024-12-28T16:51:00+00:00", False),
    ("wait, that's illegal… actually it's just against ToS", "2024-12-28T16:52:00+00:00", False),
]


def _capture_window_win32(app):
    """Capture a window's own pixels via PrintWindow (ignores occlusion)."""
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    GA_ROOT = 2
    hwnd = user32.GetAncestor(app.winfo_id(), GA_ROOT)

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
        if not user32.PrintWindow(hwnd, hdc_memory, 2):  # 2 = PW_RENDERFULLCONTENT
            raise OSError("PrintWindow failed")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_memory, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        return Image.frombuffer("RGBA", (width, height), buffer.raw, "raw", "BGRA", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_memory)
        user32.ReleaseDC(hwnd, hdc_window)


def _capture(app):
    if sys.platform == "win32":
        try:
            return _capture_window_win32(app)
        except Exception as exc:
            print(f"PrintWindow capture failed ({exc}) — falling back to screen grab")

    from PIL import ImageGrab

    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()
    app.update()
    time.sleep(0.4)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    return ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()))


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/screenshot.png")

    app = gui.DiscordToolGUI()
    app.logged_in_user = {
        "id": "123456789012345678",
        "username": "DemoUser",
        "discriminator": "0",
        "global_name": "DemoUser",
    }
    app._refresh_user_panel()
    app.entry_id.insert(0, "123456789012345678")
    app._refresh_target_title()

    messages = [
        {
            "id": str(100000 + i),
            "channel_id": "987654321098765432",
            "content": content,
            "timestamp": timestamp,
            "attachments": has_attachment,
        }
        for i, (content, timestamp, has_attachment) in enumerate(DEMO_MESSAGES)
    ]
    app._schedule_cards(messages)

    for _ in range(80):  # let card batches and rendering settle
        app.update()
        time.sleep(0.02)

    app.select_all()  # after rendering, so the checkboxes exist
    for _ in range(10):
        app.update()
        time.sleep(0.02)

    output.parent.mkdir(parents=True, exist_ok=True)
    screenshot = _capture(app).convert("RGB")
    screenshot.save(output)
    print(f"Screenshot saved to {output} ({screenshot.width}x{screenshot.height})")

    app.destroy()


if __name__ == "__main__":
    main()
