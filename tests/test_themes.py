"""Tests for the user-extensible theme subsystem (`soap.tui.themes`).

Covers: the bundled themes register with the expected names, a well-formed user
theme file in ``$SOAP_DIR/themes/`` is discovered and loaded, and every flavor
of broken/partial theme file degrades gracefully (skipped with a warning, never
raising) — mirroring how ``soap.config`` tolerates a broken ``config.yaml``.
"""

import asyncio
from pathlib import Path

from textual.color import Color

from soap.config import config_path, load_config, save_theme
from soap.library import Library
from soap.tui.app import SoapApp
from soap.tui.themes import (
    BUNDLED_THEMES,
    DEFAULT_THEME,
    load_user_themes,
    theme_from_mapping,
    themes_dir,
)


def _write_theme(soap_dir: Path, filename: str, text: str) -> None:
    directory = themes_dir(soap_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text)


# --- bundled themes -----------------------------------------------------------


def test_bundled_themes_present():
    names = {t.name for t in BUNDLED_THEMES}
    assert {"aqua-slate", "one-dark", "catppuccin-mocha"} <= names


def test_default_is_aqua_slate():
    assert DEFAULT_THEME == "aqua-slate"
    assert BUNDLED_THEMES[0].name == "aqua-slate"


# --- user theme discovery -----------------------------------------------------


def test_no_themes_dir_is_empty(tmp_path: Path):
    themes, warnings = load_user_themes(tmp_path)
    assert themes == []
    assert warnings == []


def test_loads_valid_user_theme(tmp_path: Path):
    _write_theme(
        tmp_path,
        "solar.yaml",
        "name: solarized\n"
        "dark: false\n"
        "primary: '#268bd2'\n"
        "accent: '#b58900'\n"
        "background: '#fdf6e3'\n",
    )
    themes, warnings = load_user_themes(tmp_path)
    assert warnings == []
    assert [t.name for t in themes] == ["solarized"]
    theme = themes[0]
    assert theme.primary == "#268bd2"
    assert theme.dark is False


def test_partial_theme_fills_defaults(tmp_path: Path):
    # A colors-only file (just name + primary) still loads: missing roles fall
    # back to Aqua Slate values rather than being rejected.
    _write_theme(tmp_path, "min.yaml", "name: minimal\nprimary: '#ff0000'\n")
    themes, warnings = load_user_themes(tmp_path)
    assert warnings == []
    assert len(themes) == 1
    assert themes[0].primary == "#ff0000"
    # 'text-muted' and 'border' are guaranteed so the app CSS never breaks.
    assert "text-muted" in themes[0].variables
    assert "border" in themes[0].variables


def test_malformed_yaml_skipped(tmp_path: Path):
    _write_theme(tmp_path, "broken.yaml", "name: bad\nprimary: '#fff\n:::[")
    themes, warnings = load_user_themes(tmp_path)
    assert themes == []
    assert len(warnings) == 1 and "broken.yaml" in warnings[0]


def test_non_mapping_skipped(tmp_path: Path):
    _write_theme(tmp_path, "list.yaml", "- just\n- a\n- list\n")
    themes, warnings = load_user_themes(tmp_path)
    assert themes == []
    assert "not a theme mapping" in warnings[0]


def test_missing_name_skipped(tmp_path: Path):
    _write_theme(tmp_path, "noname.yaml", "primary: '#37b3a6'\n")
    themes, warnings = load_user_themes(tmp_path)
    assert themes == []
    assert "name" in warnings[0]


def test_invalid_color_skipped(tmp_path: Path):
    _write_theme(tmp_path, "badcolor.yaml", "name: nope\nprimary: not-a-color\n")
    themes, warnings = load_user_themes(tmp_path)
    assert themes == []
    assert "hex color" in warnings[0]


def test_duplicate_name_skipped(tmp_path: Path):
    _write_theme(tmp_path, "a.yaml", "name: dup\nprimary: '#111111'\n")
    _write_theme(tmp_path, "b.yaml", "name: dup\nprimary: '#222222'\n")
    themes, warnings = load_user_themes(tmp_path)
    assert [t.name for t in themes] == ["dup"]
    assert any("duplicate" in w for w in warnings)


def test_one_broken_does_not_stop_others(tmp_path: Path):
    _write_theme(tmp_path, "good.yaml", "name: good\nprimary: '#37b3a6'\n")
    _write_theme(tmp_path, "bad.yaml", "name: bad\nprimary: zzz\n")
    themes, warnings = load_user_themes(tmp_path)
    assert [t.name for t in themes] == ["good"]
    assert len(warnings) == 1


def test_theme_from_mapping_requires_name():
    import pytest

    with pytest.raises(ValueError):
        theme_from_mapping({"primary": "#37b3a6"})


# --- config theme persistence -------------------------------------------------


def test_config_reads_theme_key(tmp_path: Path):
    config_path(tmp_path).write_text("theme: one-dark\n")
    assert load_config(tmp_path).theme == "one-dark"


def test_config_theme_defaults_none(tmp_path: Path):
    assert load_config(tmp_path).theme is None


def test_save_theme_roundtrips(tmp_path: Path):
    save_theme(tmp_path, "catppuccin-mocha")
    assert load_config(tmp_path).theme == "catppuccin-mocha"


def test_save_theme_preserves_other_keys(tmp_path: Path):
    config_path(tmp_path).write_text("# my config\nalways_review: true\n")
    save_theme(tmp_path, "one-dark")
    cfg = load_config(tmp_path)
    assert cfg.always_review is True
    assert cfg.theme == "one-dark"
    # The original comment survives the rewrite.
    assert "# my config" in config_path(tmp_path).read_text()


def test_save_theme_replaces_previous(tmp_path: Path):
    save_theme(tmp_path, "one-dark")
    save_theme(tmp_path, "aqua-slate")
    text = config_path(tmp_path).read_text()
    assert text.count("theme:") == 1
    assert load_config(tmp_path).theme == "aqua-slate"


# --- integration: the app registers themes and honors config ------------------


def _run_app(library: Library, check):
    async def main():
        app = SoapApp(library)
        async with app.run_test() as pilot:
            await pilot.pause()
            await check(pilot, app)

    asyncio.run(main())


def test_app_registers_bundled_and_user_themes(library: Library):
    _write_theme(library.path, "solar.yaml", "name: solarized\nprimary: '#268bd2'\n")

    async def check(pilot, app):
        for name in ("aqua-slate", "one-dark", "catppuccin-mocha", "solarized"):
            assert name in app.available_themes

    _run_app(library, check)


def test_app_root_is_transparent_but_theme_surfaces_are_not(library: Library):
    """Terminal background passthrough must not erase themed UI surfaces."""
    _write_theme(
        library.path,
        "solar.yaml",
        "name: solarized\n"
        "surface: '#eee8d5'\n"
        "panel: '#fdf6e3'\n",
    )

    async def check(pilot, app):
        names = [theme.name for theme in BUNDLED_THEMES] + ["solarized"]
        for name in names:
            app.theme = name
            await pilot.pause()
            assert app.styles.background.is_transparent
            assert app.screen.styles.background.is_transparent
            assert not app.query_one("#topbar").styles.background.is_transparent
            panel = app.query_one("#doclist").styles.background
            assert not panel.is_transparent
            assert panel == Color.parse(app.theme_variables["panel"])

    _run_app(library, check)


def test_app_honors_config_startup_theme(library: Library):
    config_path(library.path).write_text("theme: catppuccin-mocha\n")

    async def check(pilot, app):
        assert app.theme == "catppuccin-mocha"

    _run_app(library, check)


def test_app_unknown_config_theme_falls_back(library: Library):
    config_path(library.path).write_text("theme: does-not-exist\n")

    async def check(pilot, app):
        assert app.theme == DEFAULT_THEME

    _run_app(library, check)


def test_app_survives_malformed_user_theme(library: Library):
    _write_theme(library.path, "broken.yaml", "name: bad\nprimary: zzz\n")

    async def check(pilot, app):
        # App launched despite the broken file; default theme active.
        assert app.theme == DEFAULT_THEME
        assert app.is_running

    _run_app(library, check)


def test_app_persists_theme_change(library: Library):
    async def check(pilot, app):
        app.theme = "one-dark"
        await pilot.pause()
        assert load_config(library.path).theme == "one-dark"

    _run_app(library, check)
