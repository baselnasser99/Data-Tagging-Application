"""Dark mode: every layout loads the theme script early, and signed-in pages offer the switch."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "application" / "static"
THEME_SCRIPT = '<script src="/static/js/theme.js"></script>'


def _head(html):
    return html.split("</head>", 1)[0]


def test_theme_script_runs_before_the_page_is_drawn(client):
    # In <head> and without defer/async, so a saved choice applies before the first paint.
    for path in ("/login", "/no-such-page"):
        assert THEME_SCRIPT in _head(client.get(path).get_data(as_text=True))


def test_signed_in_layout_loads_the_theme_script_early(admin_client):
    assert THEME_SCRIPT in _head(admin_client.get("/admin/dashboard").get_data(as_text=True))


def test_account_menu_has_the_theme_switch(user_client):
    html = user_client.get("/user/dashboard").get_data(as_text=True)
    choices = re.findall(r'<button type="button" data-theme-choice="(\w+)"', html)
    assert choices == ["light", "dark", "system"]
    assert 'aria-labelledby="theme-switch-label"' in html


def test_stylesheets_define_the_dark_tokens_both_ways():
    # Followed from the system setting, and forced by the switch.
    for name in ("app.css", "auth.css"):
        css = (STATIC / "css" / name).read_text(encoding="utf-8")
        assert "@media (prefers-color-scheme: dark)" in css
        assert ':root:not([data-theme="light"])' in css
        assert ':root[data-theme="dark"]' in css
        assert "color-scheme: dark" in css
