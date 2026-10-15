"""Every stylesheet, script and image a page links to must actually load."""
import re

import pytest

from tests.conftest import login

ASSET = re.compile(rb'(?:href|src)="(/static/[^"?#]+)')


def _broken_assets(client, html):
    broken = []
    for url in sorted(set(ASSET.findall(html))):
        resp = client.get(url.decode())
        if resp.status_code != 200:
            broken.append((url.decode(), resp.status_code))
        resp.close()
    return broken


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_public_page_assets_load(client, path):
    assert _broken_assets(client, client.get(path).data) == []


def test_admin_layout_assets_load(admin_client):
    assert _broken_assets(admin_client, admin_client.get("/admin/dashboard").data) == []


def test_annotator_layout_assets_load(user_client):
    assert _broken_assets(user_client, user_client.get("/user/dashboard").data) == []
