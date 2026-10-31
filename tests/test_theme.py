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


def test_every_layout_loads_the_shared_tokens(client, admin_client):
    tokens = '<link href="/static/css/tokens.css" rel="stylesheet">'
    for resp in (admin_client.get("/admin/dashboard"), admin_client.get("/no-such-page")):
        assert tokens in _head(resp.get_data(as_text=True))
    admin_client.post("/logout")
    assert tokens in _head(client.get("/login").get_data(as_text=True))


def _declarations(css, selector):
    body = css.split(selector + " {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def test_dark_tokens_are_the_same_both_ways():
    # Followed from the system setting, and forced by the switch: the two copies must match,
    # and every dark token must exist in the light set too.
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in css
    system = _declarations(css, ':root:not([data-theme="light"])')
    forced = _declarations(css, ':root[data-theme="dark"]')
    light = _declarations(css, ":root")
    assert system and system == forced
    assert set(forced) <= set(light)
    # Colours live in tokens.css only.
    for name in ("app.css", "auth.css"):
        assert "prefers-color-scheme" not in (STATIC / "css" / name).read_text(encoding="utf-8")


def test_readme_images_follow_the_github_theme():
    # Each of the README's own images (the logo, the banner, the walkthrough and the screenshots) is
    # a light copy marked #gh-light-mode-only next to a dark copy marked #gh-dark-mode-only, so GitHub
    # picks one by its own theme, not the system's. Either may sit in its own link, as the walkthrough
    # previews do. Both files exist. Badges from other sites are left alone.
    root = STATIC.parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    pairs = re.findall(r'<img src="([^"#]+)#gh-light-mode-only"[^>]*>(?:</a><a [^>]*>)?<img src="([^"#]+)#gh-dark-mode-only"',
                       readme)
    local_images = re.findall(r'src="(docs/[^"]+)"', readme)
    assert pairs and len(local_images) == 2 * len(pairs)  # no local image outside a pair
    assert ("docs/preview/preview@2x.png", "docs/preview/preview@2x-dark.png") in pairs  # the banner
    assert "<picture>" not in readme  # it follows the system theme, even on GitHub
    for light, dark in pairs:
        base, ext = light.rsplit(".", 1)
        assert dark == f"{base}-dark.{ext}"
        assert (root / dark).is_file() and (root / light).is_file()
