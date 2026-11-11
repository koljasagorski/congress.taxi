"""Compose-Konfiguration: Proxy-Vertrauen, Port-Veröffentlichung und Log-Rotation.

Prüft die *aufgelöste* Konfiguration (``docker compose config --format json``), ohne Container zu
starten. Ohne Docker werden die Tests übersprungen (mit ``REQUIRE_OPS_TESTS=1`` schlagen sie fehl).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import API_DIR

REPO = API_DIR.parent
BASE = "docker-compose.yml"
PROXY = "docker-compose.proxy.yml"


def _require_docker() -> None:
    ok = shutil.which("docker") is not None
    if ok:
        ok = (
            subprocess.run(
                ["docker", "compose", "version"], capture_output=True, check=False, timeout=60
            ).returncode
            == 0
        )
    if not ok:
        if os.environ.get("REQUIRE_OPS_TESTS"):
            pytest.fail("Voraussetzung fehlt: docker compose")
        pytest.skip("docker compose fehlt")


def render(
    tmp_path: Path, *files: str, profile: bool = False, dotenv: str = "", **env: str
) -> dict[str, Any]:
    """Aufgelöste Konfiguration in einem Wegwerf-Projektordner.

    Die echte ``.env`` des Repos wird nie gelesen: Der Ordner enthält Kopien der Compose-Dateien,
    eine ``.env`` aus ``.env.example`` (Platzhalter) und Verweise auf ``api/`` und ``caddy/``.
    """
    _require_docker()
    project = tmp_path / "projekt"
    project.mkdir(exist_ok=True)
    for name in (BASE, PROXY):
        shutil.copy(REPO / name, project / name)
    shutil.copy(REPO / ".env.example", project / ".env")
    with (project / ".env").open("a") as fh:
        fh.write("\n" + dotenv + "\n")
    for name in ("api", "caddy", "web"):
        if not (project / name).exists():
            (project / name).symlink_to(REPO / name)
    cmd = ["docker", "compose", "--project-directory", str(project)]
    for f in files:
        cmd += ["-f", str(project / f)]
    if profile:
        cmd += ["--profile", "proxy"]
    cmd += ["config", "--format", "json"]
    # Nur PATH/HOME + Testwerte: kein Durchreichen echter Secrets aus der Umgebung
    clean = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_HOST")}
    clean.update({"POSTGRES_PASSWORD": "testwert", **env})
    r = subprocess.run(cmd, capture_output=True, text=True, env=clean, check=False, timeout=60)
    assert r.returncode == 0, r.stderr
    config: dict[str, Any] = json.loads(r.stdout)
    return config


def test_default_stack_publishes_api_on_loopback_only_and_trusts_no_proxy(tmp_path: Path) -> None:
    cfg = render(tmp_path, BASE)
    ports = cfg["services"]["api"]["ports"]
    assert [(p["host_ip"], p["target"]) for p in ports] == [("127.0.0.1", 8000)]
    assert cfg["services"]["api"]["environment"].get("TRUSTED_PROXIES", "") == ""  # aus .env
    assert "caddy" not in cfg["services"]  # Profil "proxy" aus
    assert "proxy" not in cfg["services"]["api"]["networks"]


def test_proxy_mode_hides_api_port_and_trusts_exactly_caddy(tmp_path: Path) -> None:
    # selbst ein zu weiter Wert aus .env darf den Wert der Override-Datei nicht verdrängen
    cfg = render(tmp_path, BASE, PROXY, profile=True, dotenv="TRUSTED_PROXIES=172.16.0.0/12")
    api, caddy = cfg["services"]["api"], cfg["services"]["caddy"]
    assert not api.get("ports"), api.get("ports")  # nicht auf dem Host veröffentlicht
    caddy_ip = caddy["networks"]["proxy"]["ipv4_address"]
    trusted = api["environment"]["TRUSTED_PROXIES"]
    assert ip_network(trusted, strict=True).prefixlen == 32  # genau eine Adresse, kein Netz
    assert ip_address(trusted) == ip_address(caddy_ip)
    subnet = ip_network(cfg["networks"]["proxy"]["ipam"]["config"][0]["subnet"])
    assert ip_address(caddy_ip) in subnet and subnet.prefixlen >= 24
    # Automatische Adressen (API, später Web) kommen nur aus ip_range - nie die feste von Caddy
    auto = ip_network(cfg["networks"]["proxy"]["ipam"]["config"][0]["ip_range"])
    assert auto.subnet_of(subnet) and ip_address(caddy_ip) not in auto
    # Caddy erreicht "api" nur im Netz proxy (eindeutige Adresse), die API hängt dort ebenfalls
    assert set(caddy["networks"]) == {"proxy"}
    assert "proxy" in api["networks"]
    # Nur Caddy veröffentlicht Ports (80/443), und auch der Nutzer-Compose-Pfad bleibt gültig
    published = [
        s for s, d in cfg["services"].items() if s != "db" and s != "osrm" and d.get("ports")
    ]
    assert published == ["caddy"]


def test_web_is_internal_and_reachable_for_caddy_only_in_proxy_mode(tmp_path: Path) -> None:
    # Standard: Frontend-Container nur auf Loopback veröffentlicht (Entwicklung/Rand-Proxy)
    base = render(tmp_path, BASE)["services"]["web"]
    assert [(p["host_ip"], p["target"]) for p in base["ports"]] == [("127.0.0.1", 8080)]
    # Mit Caddy: kein Host-Port mehr, aber im Netz "proxy", damit Caddy `web:8080` auflöst
    cfg = render(tmp_path, BASE, PROXY, profile=True)
    web, caddy = cfg["services"]["web"], cfg["services"]["caddy"]
    assert not web.get("ports"), web.get("ports")
    assert "proxy" in web["networks"] and "default" in web["networks"]
    assert set(caddy["depends_on"]) == {"api", "web"}
    assert web.get("read_only") is True and "ALL" in web.get("cap_drop", [])


def test_proxy_mode_follows_custom_subnet_and_address(tmp_path: Path) -> None:
    cfg = render(
        tmp_path, BASE, PROXY, profile=True,
        CADDY_SUBNET="10.99.7.0/24", CADDY_IPV4="10.99.7.9", CADDY_IP_RANGE="10.99.7.128/25",
    )  # fmt: skip
    assert cfg["networks"]["proxy"]["ipam"]["config"][0]["subnet"] == "10.99.7.0/24"
    assert cfg["services"]["caddy"]["networks"]["proxy"]["ipv4_address"] == "10.99.7.9"
    assert cfg["services"]["api"]["environment"]["TRUSTED_PROXIES"] == "10.99.7.9"


def test_every_service_rotates_its_logs(tmp_path: Path) -> None:
    cfg = render(tmp_path, BASE, PROXY, profile=True)
    for name in ("db", "osrm", "api", "web", "caddy", "cleanup"):
        logging = cfg["services"][name].get("logging")
        assert logging and logging["driver"] == "json-file", name
        assert int(logging["options"]["max-file"]) >= 2, name
        size = logging["options"]["max-size"]
        assert size.endswith(("k", "m")) and int(size[:-1]) > 0, name


def test_docs_do_not_recommend_trusting_a_whole_docker_range() -> None:
    for rel in (".env.example", "caddy/Caddyfile", "docker-compose.proxy.yml"):
        text = (REPO / rel).read_text()
        assert "TRUSTED_PROXIES=172.16.0.0/12" not in text, rel
        assert "z. B. 172.16.0.0/12" not in text, rel
    hel = (REPO / "deploy/hel/README.md").read_text()
    assert "TRUSTED_PROXIES=10.10.10.70" in hel
    assert "TRUSTED_PROXIES" in (REPO / "deploy/hel/ausrollen.sh").read_text()


def test_osrm_does_not_log_request_urls(tmp_path: Path) -> None:
    """osrm-routed protokolliert im Standard (INFO) jede Anfrage samt Koordinaten (DS-1).

    Die URL enthält Fahrer-Start (= Wohnort) und Abholpunkte mit 6 Nachkommastellen; die Logs
    liegen außerhalb der Retention. Mit ``-l WARNING`` (oder strenger) entfällt die Zeile.
    """
    cfg = render(tmp_path, BASE)
    script = "\n".join(cfg["services"]["osrm"]["command"])
    exec_lines = [ln for ln in script.splitlines() if ln.strip().startswith("exec osrm-routed")]
    assert len(exec_lines) == 1, script
    tokens = exec_lines[0].split()
    assert "-l" in tokens or "--verbosity" in tokens, exec_lines[0]
    level = tokens[(tokens.index("-l") if "-l" in tokens else tokens.index("--verbosity")) + 1]
    assert level in {"WARNING", "ERROR", "NONE"}, level
