"""Rand-Caddy (deploy/hel/): Fehlerantworten dürfen keine IP und keinen Query-String loggen.

Caddy schreibt auch OHNE ``log``-Direktive jeden Fehler der Stufe ERROR (``http.log.error``), z. B.
bei jedem 502, wenn die API nicht erreichbar ist (Ausrollen, Neustart). Der Eintrag enthält
``remote_ip``, ``client_ip``, die URI samt Query (bei ``/api/match``: lat/lon) und alle Header
(auch ``X-Forwarded-For``). Ein ``handle_errors``-Block unterdrückt ihn. Diese Tests bauen den
Rand-Block aus den Dateien im Repo mit einem unerreichbaren Upstream nach und prüfen das echte
Caddy-Log. Ohne Docker werden sie übersprungen (mit ``REQUIRE_OPS_TESTS=1`` schlagen sie fehl).
"""

from __future__ import annotations

import secrets
import time

import httpx

from tests.conftest import API_DIR
from tests.test_security_caddy import CADDY_IMAGE, _docker_ready, run

REPO = API_DIR.parent
HEL_BLOCK = REPO / "deploy" / "hel" / "sgr-caddy-block.Caddyfile"
EDGE_TEMPLATE = REPO / "caddy" / "edge-hardening.Caddyfile"

# Genau der Rand-Aufbau aus der Kommentarvorlage in edge-hardening.Caddyfile; der Upstream ist
# unerreichbar (Port 9 = discard, nichts lauscht), damit jede Anfrage ein 502 erzeugt.
EDGE_SITE = """
http://:8080 {
	@api path /api /api/*
	handle @api {
		import congress_edge_hardening no-referrer
		request_body {
			max_size 64KB
		}
		reverse_proxy 127.0.0.1:9 {
			header_up X-Forwarded-For {remote_host}
		}
	}
	handle {
		import congress_edge_hardening strict-origin-when-cross-origin
		reverse_proxy 127.0.0.1:9
	}
	import congress_error_pages
}
"""


def hel_block_for_test() -> str:
    """Den echten Rand-Block mit HTTP-Adresse und unerreichbarem Upstream versehen."""
    text = HEL_BLOCK.read_text()
    assert "congress.sgr.ski {" in text and "10.10.10.80:8000" in text
    return text.replace("congress.sgr.ski {", "http://:8080 {").replace(
        "10.10.10.80:8000", "127.0.0.1:9"
    )


def edge_template_for_test() -> str:
    return EDGE_TEMPLATE.read_text() + EDGE_SITE


def _probe(caddyfile: str, path: str) -> tuple[httpx.Response, str]:
    """Caddy mit ``caddyfile`` starten, ``path`` anfragen, Antwort und Container-Log liefern."""
    _docker_ready()
    name = f"ds2-caddy-{secrets.token_hex(4)}"
    # Konfiguration per Umgebungsvariable statt Bind-Mount (Temp-Ordner sind nicht überall im
    # Docker-Daemon sichtbar).
    r = run(
        "docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8080",
        "-e", f"CADDYFILE={caddyfile}", "--entrypoint", "sh", CADDY_IMAGE,
        "-c",
        'printf "%s" "$CADDYFILE" > /tmp/Caddyfile'
        " && exec caddy run --config /tmp/Caddyfile --adapter caddyfile",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    try:
        port = run("docker", "port", name, "8080/tcp").stdout.split(":")[-1].strip()
        url = f"http://127.0.0.1:{port}{path}"
        deadline = time.monotonic() + 60
        while True:
            try:
                resp = httpx.get(url, headers={"X-Forwarded-For": "203.0.113.77"}, timeout=10)
                break
            except httpx.HTTPError:
                assert time.monotonic() < deadline, run("docker", "logs", name).stderr
                time.sleep(0.5)
        time.sleep(1)
        logs = run("docker", "logs", name)
        return resp, logs.stdout + logs.stderr
    finally:
        run("docker", "rm", "-f", "-v", name)


SECRETS = ("203.0.113.77", "53.5701234", "9.9801234", "lat=", "remote_ip", "client_ip")


def assert_no_personal_data(logs: str) -> None:
    assert "http.log.error" not in logs, f"Fehlerlog des Rands enthält die Anfrage:\n{logs}"
    for needle in SECRETS:
        assert needle not in logs, f"{needle!r} im Rand-Log:\n{logs}"


def test_hel_edge_block_logs_no_request_details_on_502() -> None:
    resp, logs = _probe(hel_block_for_test(), "/api/health?lat=53.5701234&lon=9.9801234")
    assert resp.status_code == 502
    assert "53.5701234" not in resp.text and "dial" not in resp.text
    assert_no_personal_data(logs)


def test_edge_hardening_template_logs_no_request_details_on_502() -> None:
    resp, logs = _probe(edge_template_for_test(), "/api/match?lat=53.5701234&lon=9.9801234")
    assert resp.status_code == 502
    assert "53.5701234" not in resp.text and "dial" not in resp.text
    # Fehlerseiten tragen weiter die Härtungs-Header (genau einmal, kein Server-Header)
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers.get_list("referrer-policy") == ["no-referrer"]
    assert "server" not in resp.headers
    assert_no_personal_data(logs)


def test_edge_docs_do_not_claim_that_missing_log_directive_means_no_ips() -> None:
    # Die frühere Aussage war falsch (Fehlerlog); sie darf nicht zurückkehren.
    for path in (HEL_BLOCK, REPO / "deploy" / "hel" / "README.md", EDGE_TEMPLATE):
        text = path.read_text()
        assert "keine IPs/Query-Strings im Rand-Log" not in text, path.name
        assert "keine `log`-Direktive (keine IPs im Log)" not in text, path.name
    assert "handle_errors" in HEL_BLOCK.read_text()
    assert "congress_error_pages" in EDGE_TEMPLATE.read_text()


def test_probe_helper_detects_the_leak_without_handle_errors() -> None:
    # Positivkontrolle: Ohne handle_errors steht die Anfrage tatsächlich im Log.
    leaky = """
http://:8080 {
	handle {
		reverse_proxy 127.0.0.1:9 {
			header_up X-Forwarded-For {remote_host}
		}
	}
}
"""
    resp, logs = _probe(leaky, "/api/health?lat=53.5701234&lon=9.9801234")
    assert resp.status_code == 502
    assert "http.log.error" in logs and "53.5701234" in logs
