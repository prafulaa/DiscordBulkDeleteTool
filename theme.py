"""Visual theme assets for the GUI.

Design language inspired by Discord's dark theme and the ClearVision theme
(https://github.com/ClearVision/ClearVision-v6, Apache-2.0).

All imagery is generated procedurally at runtime with Pillow — no third-party
image assets are bundled. Users can optionally drop their own wallpaper at
``assets/background.png`` (it is darkened automatically; check the image's
license and credit its author).
"""

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# --- Palette (Discord dark + ClearVision accents) -------------------------

COLORS = {
    "rail": "#0b0c10",         # far-left icon rail
    "sidebar": "#15161c",      # channel sidebar
    "header": "#0e0f14",       # sidebar header / user panel strips
    "list": "#14161e",         # message list panel
    "card": "#22242c",         # message cards
    "input": "#0f1015",        # entry fields
    "hover": "#262933",
    "text": "#e4e6eb",
    "text_muted": "#949ba4",
    "text_faint": "#4c515c",
    "content": "#dcddde",
    "primary": "#5865F2",
    "primary_hover": "#4752c4",
    "danger": "#da373c",
    "danger_hover": "#a1282c",
    "success": "#248046",
    "success_hover": "#1a6334",
    "accent": "#2dd4bf",       # aurora teal (ClearVision vibe)
    "online": "#23a55a",
    "offline": "#80848e",
}

AVATAR_COLORS = [
    (88, 101, 242),    # blurple
    (35, 165, 90),     # green
    (240, 178, 50),    # yellow
    (242, 63, 67),     # red
    (45, 212, 191),    # teal
    (155, 89, 182),    # purple
    (233, 30, 99),     # pink
    (52, 152, 219),    # blue
]


def avatar_color_for_name(name):
    """Deterministic avatar color for a username."""
    digest = hashlib.md5((name or "?").encode("utf-8")).hexdigest()
    return AVATAR_COLORS[int(digest, 16) % len(AVATAR_COLORS)]


# --- Helpers ---------------------------------------------------------------

def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _resample():
    return Image.Resampling.LANCZOS


def _cover_resize(img, width, height):
    """Scale-crop an image so it fully covers width x height."""
    scale = max(width / img.width, height / img.height)
    size = (math.ceil(img.width * scale), math.ceil(img.height * scale))
    img = img.resize(size, _resample())
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def _load_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _vignette(width, height, strength=0.82):
    """Radial darkening mask: 255 at center, ~strength*255 at corners."""
    small_w, small_h = max(64, width // 10), max(40, height // 10)
    mask = Image.new("L", (small_w, small_h))
    pixels = mask.load()
    cx, cy = (small_w - 1) / 2, (small_h - 1) / 2
    max_d = math.hypot(cx, cy)
    for y in range(small_h):
        for x in range(small_w):
            d = math.hypot(x - cx, y - cy) / max_d
            value = 255 - int((1 - strength) * 255 * (d ** 1.6))
            pixels[x, y] = max(0, min(255, value))
    return mask.resize((width, height), _resample())


# --- Wallpaper ---------------------------------------------------------------

_BLOBS = [
    (0.72, 0.22, 0.52, (18, 88, 76)),    # teal — top right
    (0.30, 0.42, 0.55, (16, 74, 62)),    # green — mid left
    (0.52, 0.08, 0.42, (44, 50, 108)),   # indigo — top center
    (0.82, 0.72, 0.50, (26, 52, 84)),    # blue — bottom right
    (0.12, 0.85, 0.45, (20, 60, 52)),    # dark teal — bottom left
]

_DARK_OVERLAY = (13, 15, 21)


def build_wallpaper(width=2400, height=1500, seed=42):
    """Generate the aurora/nebula wallpaper. Deterministic; pure Pillow."""
    small_w, small_h = max(96, width // 10), max(60, height // 10)

    # Vertical base gradient: deep navy -> blue-slate -> deep teal-navy
    base = Image.new("RGB", (small_w, small_h))
    draw = ImageDraw.Draw(base)
    top, mid, bottom = (8, 10, 18), (13, 20, 34), (9, 15, 21)
    for y in range(small_h):
        t = y / (small_h - 1)
        color = _lerp(top, mid, t / 0.5) if t < 0.5 else _lerp(mid, bottom, (t - 0.5) / 0.5)
        draw.line([(0, y), (small_w, y)], fill=color)

    # Aurora blobs, screen-blended, then blurred into smooth glows
    overlay = Image.new("RGB", (small_w, small_h), (0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for cx, cy, radius, color in _BLOBS:
        x, y, r = cx * small_w, cy * small_h, radius * small_w
        overlay_draw.ellipse([x - r, y - r * 0.72, x + r, y + r * 0.72], fill=color)
    overlay = overlay.filter(ImageFilter.GaussianBlur(14))
    base = ImageChops.screen(base, overlay)

    img = base.resize((width, height), _resample()).filter(ImageFilter.GaussianBlur(3))

    # Vignette + final dark overlay so UI text stays readable
    img = ImageChops.multiply(img, Image.merge("RGB", (_vignette(width, height),) * 3))
    img = Image.blend(img, Image.new("RGB", (width, height), _DARK_OVERLAY), 0.40)
    return img


def prepare_background(img, width, height, darken=0.38):
    """Cover-fit a user-supplied image and darken it for UI readability."""
    img = img.convert("RGB")
    img = _cover_resize(img, width, height)
    img = ImageChops.multiply(img, Image.merge("RGB", (_vignette(width, height),) * 3))
    return Image.blend(img, Image.new("RGB", (width, height), _DARK_OVERLAY), darken)


def load_custom_background():
    """Load assets/background.png next to the script, if the user provided one."""
    custom = Path(__file__).resolve().parent / "assets" / "background.png"
    if custom.is_file():
        try:
            return Image.open(custom)
        except OSError:
            return None
    return None


# --- Avatars ------------------------------------------------------------------

def build_avatar(letter, color, size=64):
    """Discord-style circular avatar with an initial letter (RGBA)."""
    letter = (letter or "?")[0].upper()
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([0, 0, size - 1, size - 1], fill=color)

    font = _load_font(int(size * 0.52))
    left, top, right, bottom = draw.textbbox((0, 0), letter, font=font)
    text_w, text_h = right - left, bottom - top
    draw.text(
        ((size - text_w) / 2 - left, (size - text_h) / 2 - top),
        letter,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return img


def default_avatar(size=64):
    """Neutral gray avatar with a question mark (logged-out state)."""
    return build_avatar("?", (80, 84, 94), size)


# --- Derived panel colors & app icon -------------------------------------------

def sample_panel_color(wallpaper, base=(20, 22, 30), blend=0.55, box=(0.30, 0.25, 0.98, 0.95)):
    """Average the wallpaper over the message-area band and blend it toward a
    neutral dark base. Used as the list/bottom panel color so the aurora
    shows through as a subtle tint (frosted-glass illusion — tkinter has no
    per-widget alpha)."""
    width, height = wallpaper.size
    region = wallpaper.crop(
        (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
    ).resize((1, 1), _resample())
    r, g, b = region.getpixel((0, 0))[:3]
    tinted = _lerp(base, (r, g, b), 1 - blend)
    return "#{:02x}{:02x}{:02x}".format(*tinted)


def generate_app_icon(size=64):
    """Blurple rounded square with a minimal white trash-can glyph (RGBA)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=(88, 101, 242, 255))

    s = size / 64
    white = (255, 255, 255, 255)
    # Lid
    draw.rounded_rectangle([16 * s, 16 * s, 48 * s, 21 * s], radius=2 * s, fill=white)
    # Handle
    draw.rectangle([27 * s, 12 * s, 37 * s, 16 * s], fill=white)
    # Body (slightly tapered)
    draw.polygon(
        [(20 * s, 24 * s), (44 * s, 24 * s), (41 * s, 52 * s), (23 * s, 52 * s)],
        fill=white,
    )
    # Slits (cut out with the icon background color)
    cut = (88, 101, 242, 255)
    draw.rectangle([28 * s, 28 * s, 31 * s, 47 * s], fill=cut)
    draw.rectangle([33 * s, 28 * s, 36 * s, 47 * s], fill=cut)
    return img


def save_app_icon(path, size=64):
    """Write the app icon as a multi-size .ico file."""
    icon = generate_app_icon(size)
    icon.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return path


# --- Discord CDN imagery -------------------------------------------------------

_IMAGE_CACHE = {}


def circular(img):
    """Crop an image to a centered circle (RGBA)."""
    img = img.convert("RGBA")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    mask = Image.new("L", (side * 4, side * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, side * 4 - 1, side * 4 - 1], fill=255)
    mask = mask.resize((side, side), _resample())
    img.putalpha(mask)
    return img


def fetch_discord_image(url, size_px):
    """Fetch an image from the Discord CDN (icons/avatars are public) and
    return a circular RGBA PIL image, or None on any failure. Results are
    cached per URL."""
    if not url:
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    try:
        import requests

        response = requests.get(url, timeout=6)
        response.raise_for_status()
        import io

        img = Image.open(io.BytesIO(response.content)).convert("RGBA")
        img = _cover_resize(img, size_px * 2, size_px * 2)
        img = circular(img)
        _IMAGE_CACHE[url] = img
        return img
    except Exception:
        return None


def guild_icon_url(guild_id, icon_hash, size=64):
    if not icon_hash:
        return None
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.png?size={size}"


def user_avatar_url(user_id, avatar_hash, size=64):
    if not avatar_hash:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size={size}"
