"""Kachel-Proxy im Web-Container (web/nginx.conf): Datenschutz-Eigenschaften der Konfiguration.

Statische Prüfungen laufen immer; ``nginx -t`` braucht Docker (ohne Docker übersprungen, mit
``REQUIRE_OPS_TESTS=1`` schlägt es fehl). Ein Abruf bei OpenStreetMap findet bewusst nicht statt.
"""

from __future__ import annotations

import re

from tests.conftest import API_DIR
from tests.test_security_caddy import _docker_ready, run

CONF = (API_DIR.parent / "web" / "nginx.conf").read_text()
NGINX_IMAGE = "nginxinc/nginx-unprivileged:1.29-alpine"


def _tile_location() -> str:
    start = CONF.index("location ~ ")
    return CONF[start : CONF.index("\n    }\n", start)]


def test_tile_proxy_forwards_nothing_from_the_visitor() -> None:
    loc = _tile_location()
    # Weder Cookie noch X-Forwarded-For noch Referer/User-Agent des Nutzers gehen an OSM
    assert "proxy_pass_request_headers off;" in loc
    assert "proxy_pass_request_body off;" in loc
    for header in ("Cookie", "X-Forwarded-For", "X-Real-IP"):
        assert f"proxy_set_header {header}" not in loc
    assert "proxy_set_header Referer" in loc and "proxy_set_header User-Agent" in loc
    assert "congress-taxi-tile-cache" in loc


def test_tile_proxy_only_accepts_plain_tile_numbers_and_fixed_upstream() -> None:
    loc = _tile_location()
    assert loc.startswith('location ~ "^/tiles/(?<tz>[0-9]|1[0-9])/')
    assert re.search(r"proxy_pass https://tile\.openstreetmap\.org/\$tile_path;", loc)
    assert "proxy_ssl_verify on;" in loc
    assert "limit_except GET HEAD" in loc
    # alles andere unter /tiles/ ist 404, nie die SPA
    assert re.search(r"location /tiles/ \{[^}]*return 404;", CONF)


def test_tile_proxy_keeps_no_access_log() -> None:
    assert "access_log off;" in CONF


def test_nginx_accepts_the_configuration() -> None:
    _docker_ready()
    conf_path = API_DIR.parent / "web" / "nginx.conf"
    # Das Cache-Verzeichnis legt erst unser Dockerfile an: im Test auf /tmp umbiegen.
    script = (
        'printf "%s" "$CONF" | sed "s#/var/cache/tiles#/tmp/tiles#" > /tmp/default.conf'
        " && mkdir -p /tmp/tiles"
        " && printf 'pid /tmp/nginx.pid; events{} http{ include /tmp/default.conf; }'"
        " > /tmp/nginx.conf"
        " && nginx -t -c /tmp/nginx.conf"
    )
    r = run(
        "docker", "run", "--rm", "--entrypoint", "sh", "-e", f"CONF={conf_path.read_text()}",
        NGINX_IMAGE, "-c", script,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "test is successful" in r.stderr
