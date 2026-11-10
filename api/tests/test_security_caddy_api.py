"""Caddy -> echte API (Wegwerf-Aufbau): Header, Vertrauen in den Proxy, Rate-Limit, 404-Pfade.

Anders als ``test_security_caddy.py`` (nginx-Platzhalter) läuft hier das echte API-Image
(``docker build ./api``, ohne Datenbank: ``/api/health`` und ``/api/config`` brauchen keine) hinter
dem gepinnten Caddy mit dem echten Caddyfile, in einem eigenen Docker-Netz mit festem Subnetz und
fester Caddy-Adresse - wie ``docker-compose.proxy.yml``. ``TRUSTED_PROXIES`` ist genau diese /32.

Geprüft wird, was nur im Zusammenspiel sichtbar wird: keine doppelten Sicherheits-Header,
kein ``Server``-Header, HTTP -> HTTPS, ``/api/health/ready`` von außen 404 (obwohl die API selbst
antwortet), ``/api/docs`` in production 404, und dass das Rate-Limit je echtem Client zählt und sich
nicht durch ein gefälschtes ``X-Forwarded-For`` umgehen lässt. Alles wird am Ende entfernt. Ohne
Docker werden die Tests übersprungen (mit ``REQUIRE_OPS_TESTS=1`` schlagen sie fehl).
"""

from __future__ import annotations

import random
import secrets
import textwrap
import time
from collections.abc import Callable, Iterator

import httpx
import pytest

from tests.conftest import API_DIR
from tests.test_security_caddy import (
    CADDY_DIR,
    CADDY_IMAGE,
    _docker_ready,
    _nginx,
    assert_caddy_headers,
    run,
)

API_IMAGE = "p7-api-it:test"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - im Wegwerf-Container nötig, damit Caddy die API erreicht
SECURITY_HEADERS = (
    "content-security-policy",
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
)

# Kleiner HTTP-Client für Container (ohne curl): url-Teile als Argumente, Ausgabe = Statuscodes.
CLIENT_SCRIPT = textwrap.dedent(
    """
    import socket, ssl, sys
    host, port, tls = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "tls"
    path, n = sys.argv[4], int(sys.argv[5])
    spoof = sys.argv[6] == "spoof"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    codes = []
    for i in range(n):
        s = socket.create_connection((host, port), timeout=10)
        if tls:
            s = ctx.wrap_socket(s, server_hostname="localhost")
        extra = f"X-Forwarded-For: 198.51.100.{i + 1}\\r\\n" if spoof else ""
        head = f"GET {path} HTTP/1.1\\r\\nHost: localhost\\r\\n{extra}Connection: close\\r\\n\\r\\n"
        s.sendall(head.encode())
        data = b""
        while chunk := s.recv(4096):
            data += chunk
        codes.append(data.split(b" ", 2)[1].decode())
    print(",".join(codes))
    """
)


class ApiStack:
    def __init__(
        self, network: str, subnet_prefix: str, https: int, http: int, names: dict[str, str]
    ):
        self.network, self.prefix, self.https, self.http = network, subnet_prefix, https, http
        self.caddy, self.api, self.web = names["caddy"], names["api"], names["web"]
        self.caddy_ip = f"{subnet_prefix}.2"

    def request(self, method: str, path: str, **kw: object) -> httpx.Response:
        headers = {"Host": "localhost", **kw.pop("headers", {})}  # type: ignore[dict-item]
        with httpx.Client(verify=False, timeout=15) as c:  # noqa: S501 - interne Wegwerf-CA
            return c.request(
                method,
                f"https://127.0.0.1:{self.https}{path}",
                headers=headers,  # type: ignore[arg-type]
                extensions={"sni_hostname": "localhost"},
                **kw,  # type: ignore[arg-type]
            )

    def get(self, path: str, **kw: object) -> httpx.Response:
        return self.request("GET", path, **kw)

    def in_api_container(
        self, host: str, port: int, tls: str, path: str, n: int, spoof: str
    ) -> str:
        r = run(
            "docker", "exec", self.api, "python", "-c", CLIENT_SCRIPT,
            host, str(port), tls, path, str(n), spoof,
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def from_other_client(self, path: str, n: int) -> str:
        """Ein zweiter Client im selben Netz (eigene Adresse) spricht Caddy direkt an."""
        r = run(
            "docker", "run", "--rm", "--network", self.network, "--entrypoint", "python",
            API_IMAGE, "-c", CLIENT_SCRIPT, self.caddy_ip, "8443", "tls", path, str(n), "plain",
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def logs(self, container: str) -> str:
        r = run("docker", "logs", container)
        return r.stdout + r.stderr


@pytest.fixture(scope="module")
def make_api_stack() -> Iterator[Callable[..., ApiStack]]:
    _docker_ready()
    build = run("docker", "build", "-q", "-t", API_IMAGE, str(API_DIR), timeout=900)
    assert build.returncode == 0, build.stderr
    containers: list[str] = []
    networks: list[str] = []

    def factory(**api_env: str) -> ApiStack:
        suffix = secrets.token_hex(4)
        network = f"p7-it-net-{suffix}"
        for _ in range(20):  # freies /24 suchen (Überschneidungen mit Host-/Docker-Netzen)
            prefix = f"172.29.{random.randint(100, 250)}"  # noqa: S311 - kein Sicherheitszweck
            if (
                run("docker", "network", "create", "--subnet", f"{prefix}.0/24", network).returncode
                == 0
            ):
                break
        else:
            pytest.skip("kein freies Docker-Subnetz gefunden")
        networks.append(network)
        names = {k: f"p7-it-{k}-{suffix}" for k in ("caddy", "api", "web")}
        caddy_ip = f"{prefix}.2"
        env = {
            "APP_ENV": "production",
            "JWT_SECRET": secrets.token_hex(32),
            "POSTGRES_PASSWORD": secrets.token_hex(16),
            "POSTGRES_HOST": "db.invalid",
            "COOKIE_SECURE": "true",
            "DOMAIN": "localhost",
            "TRUSTED_PROXIES": caddy_ip,  # genau die feste Caddy-Adresse (/32)
            **api_env,
        }
        # Caddy zuerst: Docker vergibt die Adressen der Reihe nach, .2 soll ihm gehören.
        cmd = [
            "docker", "run", "-d", "--rm", "--name", names["caddy"], "--network", network,
            "--ip", caddy_ip, "-p", "127.0.0.1::8443", "-p", "127.0.0.1::8088",
            "-v", f"{CADDY_DIR}:/etc/caddy:ro",
            "-e", "CADDY_SITE_ADDRESS=localhost", "-e", "HTTP_PORT=8088", "-e", "HTTPS_PORT=8443",
            "-e", "TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "--entrypoint", "/bin/sh", CADDY_IMAGE, "/etc/caddy/entrypoint.sh",
        ]  # fmt: skip
        containers.append(names["caddy"])
        r = run(*cmd)
        assert r.returncode == 0, r.stderr
        cmd = ["docker", "run", "-d", "--name", names["api"], "--network", network,
               "--network-alias", "api"]  # fmt: skip
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        # wie CMD im Dockerfile, aber ohne Migrationen (keine Datenbank in diesem Aufbau)
        cmd += ["--entrypoint", "uvicorn", API_IMAGE, "app.main:app", "--host", ALL_INTERFACES,
                "--port", "8000", "--workers", "1", "--no-access-log",
                "--no-proxy-headers"]  # fmt: skip
        containers.append(names["api"])
        r = run(*cmd)
        assert r.returncode == 0, r.stderr
        containers.append(names["web"])
        _nginx(names["web"], network, "web", 8080)
        https = int(run("docker", "port", names["caddy"], "8443/tcp").stdout.split(":")[-1])
        http = int(run("docker", "port", names["caddy"], "8088/tcp").stdout.split(":")[-1])
        stack = ApiStack(network, prefix, https, http, names)
        deadline = time.monotonic() + 120
        while True:
            try:
                if stack.get("/api/health").status_code == 200:
                    return stack
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, (
                f"Stack wurde nicht bereit:\nAPI:\n{stack.logs(names['api'])}\n"
                f"Caddy:\n{stack.logs(names['caddy'])}"
            )
            time.sleep(1)

    yield factory
    for name in containers:
        run("docker", "rm", "-f", "-v", name)
    for network in networks:
        run("docker", "network", "rm", network)


@pytest.fixture(scope="module")
def stack(make_api_stack: Callable[..., ApiStack]) -> ApiStack:
    return make_api_stack(RATE_LIMIT_ENABLED="false")


def test_real_api_behind_caddy_has_each_security_header_exactly_once(stack: ApiStack) -> None:
    r = stack.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert_caddy_headers(r)
    for name in SECURITY_HEADERS:
        assert len(r.headers.get_list(name)) == 1, f"{name}: {r.headers.get_list(name)}"
    assert r.headers["cache-control"] == "no-store"  # kommt von der API
    assert not {"server", "via", "x-powered-by"} & set(r.headers)
    # Header auch an Fehlerantworten der API (404) und am 405
    nf = stack.get("/api/gibt-es-nicht")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "not_found"
    for name in SECURITY_HEADERS:
        assert len(nf.headers.get_list(name)) == 1, name


def test_ready_is_404_from_outside_but_alive_inside(stack: ApiStack) -> None:
    for path in ("/api/health/ready", "/api/health/ready/"):
        r = stack.get(path)
        assert r.status_code == 404
        assert_caddy_headers(r)
        assert "postgis" not in r.text.lower() and "database" not in r.text.lower()
    # Gegenprobe: die API selbst kennt den Pfad (503/200 je nach DB/OSRM, jedenfalls nicht 404)
    inside = stack.in_api_container("127.0.0.1", 8000, "plain", "/api/health/ready", 1, "plain")
    assert inside in {"200", "503"}, inside


def test_docs_and_openapi_are_404_in_production(stack: ApiStack) -> None:
    for path in ("/api/docs", "/api/openapi.json", "/api/redoc"):
        r = stack.get(path)
        assert r.status_code == 404, path
        assert r.json()["error"]["code"] == "not_found"
        assert "swagger" not in r.text.lower() and "jsdelivr" not in r.text.lower()


def test_http_is_redirected_to_https(stack: ApiStack) -> None:
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        r = c.get(f"http://127.0.0.1:{stack.http}/api/health?x=1", headers={"Host": "localhost"})
    assert r.status_code in (301, 308)
    assert r.headers["location"].startswith("https://localhost")
    assert r.content == b""
    # Bekannte Grenze (siehe PLAN.md): Caddys automatische Weiterleitung entsteht außerhalb der
    # Site-Handler, trägt daher weder die Sicherheits-Header noch lässt sich "Server: Caddy" dort
    # entfernen. Sie enthält keine Daten (leerer Body, nur der Pfad im Location-Header).


def test_oversized_body_is_rejected_before_it_reaches_the_api(stack: ApiStack) -> None:
    r = stack.request("POST", "/api/auth/login", content=b"x" * 200_000)
    assert r.status_code == 413
    assert_caddy_headers(r)


def test_rate_limit_counts_per_real_client_and_ignores_spoofed_forwarded_for(
    make_api_stack: Callable[..., ApiStack],
) -> None:
    s = make_api_stack(RATE_LIMIT_API_PER_MIN="4")
    # Client 1 (Host) rät mit wechselnden, gefälschten X-Forwarded-For-Werten: Caddy überschreibt
    # sie (der Host ist kein vertrauter Proxy), alle Anfragen zählen auf EINEN Client.
    codes = [
        s.get("/api/config", headers={"X-Forwarded-For": f"198.51.100.{i + 1}"}).status_code
        for i in range(6)
    ]
    assert codes == [200, 200, 200, 200, 429, 429], codes
    blocked = s.get("/api/config")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert_caddy_headers(blocked)  # auch das 429 trägt alle Header (genau einmal)
    assert len(blocked.headers.get_list("x-content-type-options")) == 1
    # Client 2 (anderer Container, andere Adresse) hat einen eigenen Zähler: die API glaubt Caddys
    # X-Forwarded-For (TRUSTED_PROXIES = Caddys /32) und zählt nicht alle als "Caddy".
    assert s.from_other_client("/api/config", 4) == "200,200,200,200"
    assert s.from_other_client("/api/config", 1) == "429"
    # Ein Container im Netz, der die API direkt anspricht, kann sich nicht per X-Forwarded-For eine
    # frische Adresse geben: sein Peer ist nicht Caddys /32, der Header wird ignoriert.
    direct = s.in_api_container("127.0.0.1", 8000, "plain", "/api/config", 6, "spoof")
    assert direct == "200,200,200,200,429,429", direct
    # Health bleibt vom Limit ausgenommen (Docker-Healthcheck)
    assert s.get("/api/health").status_code == 200


def test_image_cmd_disables_uvicorns_own_proxy_header_handling() -> None:
    """Uvicorn würde X-Forwarded-For von 127.0.0.1 glauben und die Client-Adresse überschreiben."""
    cmd_line = next(
        line for line in (API_DIR / "Dockerfile").read_text().splitlines() if line.startswith("CMD")
    )
    assert "--no-proxy-headers" in cmd_line and "--workers" in cmd_line
