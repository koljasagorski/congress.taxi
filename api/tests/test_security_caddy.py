"""Caddy: Tile-Origin-Ableitung, Caddyfile-Validierung und ein Wegwerf-Aufbau mit echten Headern.

Der Wegwerf-Aufbau startet ein eigenes Docker-Netz mit zufälligem Namen, zwei nginx-Platzhaltern
(``api`` auf 8000, ``web`` auf 8080) und den gepinnten Caddy-Container mit dem echten Caddyfile.
Alles wird am Ende entfernt. Ohne Docker werden die Docker-Tests übersprungen (mit
``REQUIRE_OPS_TESTS=1`` schlagen sie fehl).
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import API_DIR

REPO = API_DIR.parent
CADDY_DIR = REPO / "caddy"
TILE_SCRIPT = CADDY_DIR / "tile-origin.sh"
CADDY_IMAGE = "caddy:2.11.8-alpine"
NGINX_IMAGE = "nginx:alpine"


def _require(ok: bool, reason: str) -> None:
    if ok:
        return
    if os.environ.get("REQUIRE_OPS_TESTS"):
        pytest.fail(f"Voraussetzung fehlt: {reason}")
    pytest.skip(reason)


def run(*cmd: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# --- Tile-Origin (ohne Docker) -----------------------------------------------------------------


def tile_origin(url: str) -> subprocess.CompletedProcess[str]:
    return run("sh", str(TILE_SCRIPT), url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "https://tile.openstreetmap.org"),
        ("https://{s}.tile.example.org/{z}/{x}/{y}.png", "https://*.tile.example.org"),
        (
            "https://tiles.example.org:8443/{z}/{x}/{y}.png?key=abc",
            "https://tiles.example.org:8443",
        ),
        ("http://localhost:8081/t/{z}.png", "http://localhost:8081"),
        ("https://tiles.example.org", "https://tiles.example.org"),
        ("/tiles/{z}/{x}/{y}.png", ""),
        ("./tiles/{z}/{x}/{y}.png", ""),
        ("", ""),
    ],
)
def test_tile_origin_derivation(url: str, expected: str) -> None:
    r = tile_origin(url)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.org;script-src *",
        "https://evil.org' 'unsafe-eval",
        "https://a b.org/x",
        'https://evil.org"/x',
        "https://user:pw@host.example/x",
        "https://host.example\nContent-Security-Policy: x",
        "https://{evil}.example.org/x",
        "https://*.example.org/{z}.png",
        "https://[::1]/x",
        "ftp://host.example/x",
        "javascript:alert(1)",
        "tile.example.org/{z}.png",
        "https://",
        "https:///pfad",
        "https://-bad.example.org/x",
        "https://host.example:99999999/x",
    ],
)
def test_tile_origin_rejects_dangerous_values(url: str) -> None:
    r = tile_origin(url)
    assert r.returncode == 1, f"{url!r} -> {r.stdout!r}"
    assert r.stdout == ""
    assert "ungültig" in r.stderr


# --- Docker: Validierung und Wegwerf-Aufbau ----------------------------------------------------


def _docker_ready() -> None:
    _require(shutil.which("docker") is not None, "docker fehlt")
    info = run("docker", "info", "--format", "{{.ServerVersion}}")
    _require(info.returncode == 0, "Docker-Daemon nicht erreichbar")


def caddy_run(
    *args: str, env: dict[str, str] | None = None, name: str
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "run", "--rm", "--name", name, "-v", f"{CADDY_DIR}:/etc/caddy:ro"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    return run(*cmd, CADDY_IMAGE, *args, timeout=300)


def test_caddyfile_validates_with_and_without_tile_origin() -> None:
    _docker_ready()
    for origin in ("https://tile.example.org", ""):
        r = caddy_run(
            "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile",
            env={"TILE_ORIGIN": origin}, name=f"p7-caddy-validate-{secrets.token_hex(3)}",
        )  # fmt: skip
        assert r.returncode == 0, r.stdout + r.stderr


def test_caddyfile_is_formatted() -> None:
    _docker_ready()
    name = f"p7-caddy-fmt-{secrets.token_hex(3)}"
    r = caddy_run("caddy", "fmt", "--diff", "/etc/caddy/Caddyfile", name=name)
    assert r.returncode == 0, r.stdout + r.stderr


def test_entrypoint_refuses_to_start_with_invalid_tile_url() -> None:
    _docker_ready()
    r = caddy_run(
        "/bin/sh", "/etc/caddy/entrypoint.sh",
        env={"TILE_URL": "https://evil.org;script-src *"},
        name=f"p7-caddy-entry-{secrets.token_hex(3)}",
    )  # fmt: skip
    assert r.returncode == 1
    assert "TILE_URL" in r.stderr


NGINX_CONF = (
    "server { listen %d; location / { default_type text/plain; "
    'return 200 "xff=$http_x_forwarded_for uri=$request_uri\\n"; } }'
)


class Stack:
    def __init__(self, network: str, port: int, caddy: str, api: str, web: str) -> None:
        self.network, self.port, self.caddy, self.api, self.web = network, port, caddy, api, web

    def get(self, path: str, **kw: object) -> httpx.Response:
        return self.request("GET", path, **kw)

    def request(self, method: str, path: str, **kw: object) -> httpx.Response:
        headers = {"Host": "localhost", **kw.pop("headers", {})}  # type: ignore[dict-item]
        with httpx.Client(verify=False, timeout=15) as c:  # noqa: S501 - interne Wegwerf-CA
            return c.request(
                method,
                f"https://127.0.0.1:{self.port}{path}",
                headers=headers,  # type: ignore[arg-type]
                extensions={"sni_hostname": "localhost"},
                **kw,  # type: ignore[arg-type]
            )

    def logs(self) -> str:
        r = run("docker", "logs", self.caddy)
        return r.stdout + r.stderr


def _nginx(name: str, network: str, alias: str, port: int) -> None:
    r = run(
        "docker", "run", "-d", "--name", name, "--network", network,
        "--network-alias", alias, "-e", f"CONF={NGINX_CONF % port}",
        "--entrypoint", "sh", NGINX_IMAGE,
        "-c", 'printf "%s" "$CONF" > /etc/nginx/conf.d/default.conf && exec nginx -g "daemon off;"',
    )  # fmt: skip
    assert r.returncode == 0, r.stderr


@pytest.fixture(scope="module")
def make_stack() -> Iterator[Callable[..., Stack]]:
    _docker_ready()
    created: list[str] = []
    networks: list[str] = []

    def factory(**env: str) -> Stack:
        suffix = secrets.token_hex(4)
        network = f"p7-caddy-net-{suffix}"
        caddy, api, web = (f"p7-caddy-{suffix}", f"p7-api-{suffix}", f"p7-web-{suffix}")
        assert run("docker", "network", "create", network).returncode == 0
        networks.append(network)
        for name, alias, port in ((api, "api", 8000), (web, "web", 8080)):
            created.append(name)
            _nginx(name, network, alias, port)
        created.append(caddy)
        cmd = [
            "docker", "run", "-d", "--rm", "--name", caddy, "--network", network,
            "-p", "127.0.0.1::8443", "-v", f"{CADDY_DIR}:/etc/caddy:ro",
            "-e", "CADDY_SITE_ADDRESS=localhost", "-e", "HTTP_PORT=8088", "-e", "HTTPS_PORT=8443",
            "-e", "TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        ]  # fmt: skip
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["--entrypoint", "/bin/sh", CADDY_IMAGE, "/etc/caddy/entrypoint.sh"]
        r = run(*cmd)
        assert r.returncode == 0, r.stderr
        mapped = run("docker", "port", caddy, "8443/tcp").stdout.split(":")[-1].strip()
        stack = Stack(network, int(mapped), caddy, api, web)
        deadline = time.monotonic() + 90
        while True:
            try:
                if stack.get("/").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, f"Caddy wurde nicht bereit:\n{stack.logs()}"
            time.sleep(1)
        return stack

    yield factory
    for name in created:
        run("docker", "rm", "-f", "-v", name)  # -v: anonyme Image-Volumes mitlöschen
    for network in networks:
        run("docker", "network", "rm", network)


@pytest.fixture(scope="module")
def stack(make_stack: Callable[..., Stack]) -> Stack:
    return make_stack()


def csp_directives(value: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in value.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def assert_caddy_headers(r: httpx.Response) -> None:
    csp = csp_directives(r.headers["content-security-policy"])
    assert csp["default-src"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["script-src"] == ["'self'"]  # kein unsafe-inline/-eval
    assert "'unsafe-inline'" in csp["style-src"]  # Leaflet
    assert csp["img-src"] == ["'self'", "data:", "https://tile.openstreetmap.org"]
    assert "unsafe-eval" not in r.headers["content-security-policy"]
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "geolocation=(self)" in r.headers["permissions-policy"]
    assert "server" not in r.headers and "via" not in r.headers


def test_headers_on_api_and_frontend_routes(stack: Stack) -> None:
    api = stack.get("/api/health", params={"lat": "53.55", "lon": "9.99"})
    assert api.status_code == 200 and "uri=/api/health?lat=53.55" in api.text  # ging an "api"
    assert_caddy_headers(api)
    web = stack.get("/karte")
    assert web.status_code == 200 and "uri=/karte" in web.text  # ging an "web"
    assert_caddy_headers(web)
    assert_caddy_headers(stack.get("/api"))  # ohne Slash ebenfalls zur API


def test_request_body_limit_on_api(stack: Stack) -> None:
    ok = stack.request("POST", "/api/x", content=b"x" * 1000)
    assert ok.status_code == 200
    big = stack.request("POST", "/api/x", content=b"x" * 200_000)
    assert big.status_code == 413
    assert_caddy_headers(big)


def test_forwarded_for_from_untrusted_client_is_overwritten(stack: Stack) -> None:
    r = stack.get("/api/health", headers={"X-Forwarded-For": "6.6.6.6"})
    xff = re.search(r"xff=(\S*)", r.text)
    assert xff is not None
    assert "6.6.6.6" not in xff.group(1)  # gefälschter Wert wird nicht weitergereicht
    assert re.fullmatch(r"[0-9a-f.:]+", xff.group(1))  # genau die echte Peer-Adresse


def test_forwarded_for_is_kept_when_the_peer_is_a_trusted_proxy(
    make_stack: Callable[..., Stack],
) -> None:
    trusted = make_stack(CADDY_TRUSTED_PROXIES="0.0.0.0/0 ::/0")
    r = trusted.get("/api/health", headers={"X-Forwarded-For": "198.51.100.7"})
    assert "xff=198.51.100.7," in r.text  # Rand-Proxy-Eintrag bleibt, Peer wird angehängt


def test_error_pages_carry_headers_and_logs_contain_no_personal_data(stack: Stack) -> None:
    # Peer-Adresse aus Sicht von Caddy = Eintrag, den das Backend als X-Forwarded-For sieht
    seen = stack.get("/api/health").text
    peer = re.search(r"xff=(\S+)", seen)
    assert peer is not None
    peer_ip = peer.group(1)
    assert run("docker", "stop", stack.api).returncode == 0
    try:
        down = stack.get("/api/match", params={"lat": "53.551086", "lon": "9.993682"})
        assert down.status_code == 502
        assert_caddy_headers(down)  # auch Fehlerseiten tragen die Header
        assert "53.551086" not in down.text and "upstream" not in down.text.lower()
        # fehlerhafte TLS-Verbindung (Klartext auf TLS-Port) erzeugt Handshake-Fehler im Server
        with httpx.Client(timeout=5) as c, contextlib.suppress(httpx.HTTPError):
            c.get(f"http://127.0.0.1:{stack.port}/api/match?lat=53.551086&lon=9.993682")
    finally:
        assert run("docker", "start", stack.api).returncode == 0
    time.sleep(1)
    logs = stack.logs()
    forbidden = (peer_ip, "53.551086", "9.993682", "lat=", "/api/match", "remote_ip", "client_ip")
    for needle in forbidden:
        assert needle not in logs, f"{needle!r} im Caddy-Log:\n{logs}"


def test_no_access_log_lines_for_normal_requests(stack: Stack) -> None:
    for _ in range(3):
        stack.get("/api/health", params={"lat": "53.5", "lon": "9.9"})
    logs = stack.logs()
    assert "handled request" not in logs
    assert "/api/health" not in logs


def test_container_stays_up(stack: Stack) -> None:
    state = run("docker", "inspect", "-f", "{{.State.Running}}", stack.caddy).stdout.strip()
    assert state == "true", Path(CADDY_DIR).name
