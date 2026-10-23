"""Regression: an updated page must not reuse the previous CSS/JS cache key."""

import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from app import assets
from tests.helpers import login


def test_content_change_updates_asset_url_even_with_preserved_size_and_mtime(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(assets, "STATIC_ROOT", tmp_path)
    path = tmp_path / "family.css"
    path.write_bytes(b"a{color:red}")
    stat = path.stat()
    old_url = assets.asset_url("family.css")
    assert assets.asset_url("family.css") == old_url
    path.write_bytes(b"a{color:tan}")  # Same size, including on Windows.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert assets.asset_url("family.css") != old_url


@pytest.mark.parametrize("name", ["../main.py", "../static/../main.py", "icons.svg"])
def test_only_public_script_and_style_paths_are_versioned(name):
    with pytest.raises(ValueError):
        assets.asset_url(name)


def test_both_family_views_load_their_own_versioned_style_and_draw_unfilled_paths(
    client,
):
    for role in ("patient1", "doctor"):
        login(client, role)
        response = client.get("/patients/1/profile/family")
        assert response.status_code == 200
        urls = re.findall(
            r'(?:src|href)="(/static/[^"#]+\.(?:css|js)(?:\?[^"#]+)?)"', response.text
        )
        assert assets.asset_url("family.css") in urls
        assert assets.asset_url("intake.css") in urls
        for url in urls:
            assert len(parse_qs(urlsplit(url).query)["v"][0]) == 16
            assert client.get(url).status_code == 200
        assert assets.asset_url("family.js") in urls
        if role == "doctor":
            assert (
                'class="family-connectors" width="0" height="0" fill="none"'
                in response.text
            )
            assert "--family-width:" in response.text
        else:
            assert 'class="tree-art" width="0" height="0"' in response.text
            assert 'class="tree-branches" fill="none"' in response.text
            assert "--garden-height:" in response.text
        assert '<ul class="family-node-list">' in response.text
        assert (
            'style="width:'
            not in response.text.split('class="family-diagram', 1)[1].split(">", 1)[0]
        )
