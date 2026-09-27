from pathlib import Path

from PIL import Image

import theme


def test_build_wallpaper_size_and_determinism():
    wp1 = theme.build_wallpaper(400, 250)
    wp2 = theme.build_wallpaper(400, 250)
    assert wp1.size == (400, 250)
    assert wp1.mode == "RGB"
    assert wp1.tobytes() == wp2.tobytes()  # deterministic seed


def test_build_wallpaper_is_dark_enough_for_text():
    wp = theme.build_wallpaper(200, 200)
    extrema = wp.convert("L").getextrema()
    assert extrema[1] < 220  # no blinding highlights


def test_avatar_color_for_name_stable():
    assert theme.avatar_color_for_name("alice") == theme.avatar_color_for_name("alice")
    assert theme.avatar_color_for_name("alice") in theme.AVATAR_COLORS
    assert theme.avatar_color_for_name("") in theme.AVATAR_COLORS


def test_build_avatar_circle_with_initial():
    avatar = theme.build_avatar("bob", (88, 101, 242), 64)
    assert avatar.mode == "RGBA"
    assert avatar.size == (64, 64)
    corner = avatar.getpixel((1, 1))
    center = avatar.getpixel((32, 32))
    assert corner[3] == 0        # outside the circle is transparent
    assert center[3] == 255      # inside the circle is opaque


def test_default_avatar():
    avatar = theme.default_avatar(48)
    assert avatar.size == (48, 48)
    assert avatar.getpixel((24, 24))[3] == 255


def test_prepare_background_covers_and_darkens(tmp_path):
    source = Image.new("RGB", (50, 50), (255, 255, 255))
    result = theme.prepare_background(source, 200, 100)
    assert result.size == (200, 100)
    extrema = result.convert("L").getextrema()
    assert extrema[1] < 255  # white input got darkened


def test_load_custom_background_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(theme, "__file__", str(tmp_path / "theme.py"))
    assert theme.load_custom_background() is None


def test_load_custom_background_present(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (30, 30), (10, 10, 10)).save(assets / "background.png")
    monkeypatch.setattr(theme, "__file__", str(tmp_path / "theme.py"))
    loaded = theme.load_custom_background()
    assert isinstance(loaded, Image.Image)
    assert Path(loaded.filename).name == "background.png"


def test_sample_panel_color_returns_dark_hex():
    wallpaper = theme.build_wallpaper(300, 200)
    tint = theme.sample_panel_color(wallpaper)
    assert tint.startswith("#") and len(tint) == 7
    r, g, b = int(tint[1:3], 16), int(tint[3:5], 16), int(tint[5:7], 16)
    assert max(r, g, b) < 90  # stays dark enough for light text


def test_generate_app_icon_rounded_and_opaque():
    icon = theme.generate_app_icon(64)
    assert icon.mode == "RGBA"
    assert icon.getpixel((2, 2))[3] == 0        # outside the rounded square
    assert icon.getpixel((32, 32))[3] == 255    # inside the square


def test_save_app_icon(tmp_path):
    target = tmp_path / "icon.ico"
    theme.save_app_icon(str(target))
    assert target.is_file() and target.stat().st_size > 0
