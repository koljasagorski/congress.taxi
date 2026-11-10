"""scripts/seed_demo.py: reine Logik und HTTP-Ablauf gegen ``httpx.MockTransport``.

Die Endpunkte (Phase 2/3) gibt es inzwischen; ``FakeApi`` bildet den
Vertrag (docs/API-CONTRACT.md) nach: Register 202 ohne Auto-Login, Login mit Secure-Cookie,
``GET /api/rides/mine``, ``POST /api/rides``, ``GET /api/match``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from email_validator import EmailNotValidError

from app.schemas.auth import _validate_address

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_demo_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses mit __future__-Annotationen brauchen den Eintrag
    spec.loader.exec_module(module)
    return module


seed_demo = load_script()
NOW = datetime(2026, 11, 10, 15, 30, tzinfo=UTC)


class FakeApi:
    """Minimaler In-Memory-Server nach Vertrag."""

    def __init__(self, *, secure_cookie: bool = True) -> None:
        self.users: dict[str, dict[str, str]] = {}
        self.rides: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.secure_cookie = secure_cookie
        self.login_limit: int | None = None  # nach so vielen Logins 429
        self.logins = 0
        self.register_status = 202
        self.match_status = 200

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _user(self, request: httpx.Request) -> str | None:
        cookie = request.headers.get("cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "access_token" and value.startswith("tok-"):
                return value[4:]
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        if path == "/api/auth/register":
            if self.register_status != 202:
                return self._error(self.register_status, "validation_error")
            if body["email"] not in self.users:
                assert body["consent"] is True and 10 <= len(body["password"]) <= 128
                self.users[body["email"]] = {
                    "password": body["password"],
                    "name": body["display_name"],
                }
            return httpx.Response(202, json={"message": "ok"})
        if path == "/api/auth/login":
            self.logins += 1
            if self.login_limit is not None and self.logins > self.login_limit:
                return self._error(429, "rate_limited", {"Retry-After": "2"})
            user = self.users.get(body["email"])
            if user is None or user["password"] != body["password"]:
                return self._error(401, "invalid_credentials")
            secure = "; Secure" if self.secure_cookie else ""
            return httpx.Response(
                200,
                json={"user": {"id": "x", "display_name": user["name"]}},
                headers={
                    "set-cookie": f"access_token=tok-{body['email']}; HttpOnly; Path=/{secure}"
                },
            )
        email = self._user(request)
        if email is None:
            return self._error(401, "unauthorized")
        if path == "/api/rides/mine":
            items = self.rides.get(email, [])
            return httpx.Response(
                200, json={"items": items, "total": len(items), "limit": 100, "offset": 0}
            )
        if path == "/api/rides" and request.method == "POST":
            assert body["start"].keys() == {"lat", "lon"}
            assert datetime.fromisoformat(body["departure_time"]).tzinfo is not None
            self.rides.setdefault(email, []).append(
                {
                    "status": "open",
                    "departure_time": body["departure_time"],
                    "seats_total": body["seats"],
                }
            )
            return httpx.Response(201, json={"id": "r"})
        if path == "/api/match":
            if self.match_status != 200:
                return self._error(self.match_status, "routing_unavailable")
            return httpx.Response(200, json={"items": [], "total": 4})
        return self._error(404, "not_found")

    @staticmethod
    def _error(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": code, "message": "x"}}, headers=headers
        )


def run_seed(api: FakeApi, **kw: Any) -> Any:
    return seed_demo.seed("http://127.0.0.1:8000", transport=api.transport(), now=NOW, **kw)


# --- reine Teile --------------------------------------------------------------------------------


def test_demo_data_is_well_formed() -> None:
    users = seed_demo.DEMO_USERS
    assert len({u.email for u in users}) == len(users)
    assert all(u.email.endswith("@demo.invalid") for u in users)
    # Die Registrierung (Phase 2) erlaubt reservierte Domains wie ``.invalid`` außerhalb von
    # Produktion – jede Demo-Adresse muss mit dem echten Validator der API durchgehen.
    for u in users:
        assert _validate_address(u.email, special_use_ok=True) == u.email
        with pytest.raises(EmailNotValidError):  # in Produktion strikt abgelehnt
            _validate_address(u.email, special_use_ok=False)
    assert all(10 <= len(u.password) <= 128 for u in users)
    assert all(1 <= len(u.display_name) <= 80 for u in users)
    drivers = {u.email for u in seed_demo.DEMO_DRIVERS}
    assert {r.driver_email for r in seed_demo.DEMO_RIDES} == drivers
    assert len(users) <= 8, "bleibt unter dem Registrierungs-Limit (10/Stunde)"
    for ride in seed_demo.DEMO_RIDES:
        assert 1 <= ride.seats <= 8 and ride.day_offset in (1, 2)
        lat, lon = ride.start
        assert 53.3 < lat < 53.8 and 9.7 < lon < 10.4  # Hamburg-Extrakt (grob)
    assert {r.day_offset for r in seed_demo.DEMO_RIDES} == {1, 2}
    assert len({r.start for r in seed_demo.DEMO_RIDES}) == len(seed_demo.DEMO_RIDES)


def test_departure_is_tomorrow_or_day_after_in_utc_and_in_the_future() -> None:
    late = datetime(2026, 11, 10, 23, 59, tzinfo=UTC)
    for ride in seed_demo.DEMO_RIDES:
        dep = seed_demo.departure_time(ride, late)
        assert dep.tzinfo is not None and dep > late + timedelta(hours=5)
        assert dep.date() == (late + timedelta(days=ride.day_offset)).date()


def test_refuses_production_from_environment_and_env_file(tmp_path: Path) -> None:
    with pytest.raises(seed_demo.RefusedError):
        seed_demo.refuse_in_production({"APP_ENV": "production"}, None)
    with pytest.raises(seed_demo.RefusedError):
        seed_demo.refuse_in_production({"APP_ENV": "Production "}, None)
    env_file = tmp_path / ".env"
    env_file.write_text("# Kommentar\nAPP_ENV='production'\nDOMAIN=x\n")
    with pytest.raises(seed_demo.RefusedError):  # Prozess-Umgebung leer, .env sagt Produktion
        seed_demo.refuse_in_production({}, env_file)
    env_file.write_text("APP_ENV=development\n")
    seed_demo.refuse_in_production({}, env_file)
    seed_demo.refuse_in_production({"APP_ENV": "development"}, tmp_path / "gibt-es-nicht")


def test_remote_targets_need_an_explicit_flag() -> None:
    for ok in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
        seed_demo.check_target(ok, allow_remote=False)
    for bad in ("https://congress.example.org", "http://10.0.0.5:8000"):
        with pytest.raises(seed_demo.RefusedError):
            seed_demo.check_target(bad, allow_remote=False)
        seed_demo.check_target(bad, allow_remote=True)


def test_default_base_url_uses_env_and_env_file(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("API_PORT=18000\n")
    assert seed_demo.default_base_url({}, f) == "http://127.0.0.1:18000"
    assert seed_demo.default_base_url({"API_PORT": "9000"}, f) == "http://127.0.0.1:9000"
    assert (
        seed_demo.default_base_url({"SEED_API_URL": "http://localhost:1/"}, f)
        == "http://localhost:1"
    )
    assert seed_demo.default_base_url({}, None) == "http://127.0.0.1:8000"


# --- HTTP-Ablauf --------------------------------------------------------------------------------


def test_first_run_registers_logs_in_and_creates_rides() -> None:
    api = FakeApi()
    report = run_seed(api)
    n_users, n_rides = len(seed_demo.DEMO_USERS), len(seed_demo.DEMO_RIDES)
    assert report.users_registered == n_users and report.users_existing == 0
    assert report.rides_created == n_rides and report.rides_skipped == 0
    assert report.match_probe == 4
    assert set(api.users) == {u.email for u in seed_demo.DEMO_USERS}
    assert sum(len(v) for v in api.rides.values()) == n_rides
    assert ("POST", "/api/auth/register") in api.requests


def test_second_run_is_idempotent_and_does_not_register_again() -> None:
    api = FakeApi()
    run_seed(api)
    registers_before = api.requests.count(("POST", "/api/auth/register"))
    posts_before = api.requests.count(("POST", "/api/rides"))
    report = run_seed(api)
    assert report.users_registered == 0 and report.users_existing == len(seed_demo.DEMO_USERS)
    assert report.rides_created == 0 and report.rides_skipped == len(seed_demo.DEMO_RIDES)
    assert api.requests.count(("POST", "/api/auth/register")) == registers_before
    assert api.requests.count(("POST", "/api/rides")) == posts_before


def test_cancelled_or_past_rides_do_not_count_as_existing() -> None:
    api = FakeApi()
    run_seed(api)
    driver = seed_demo.DEMO_DRIVERS[0].email
    api.rides[driver] = [
        {"status": "cancelled", "departure_time": (NOW + timedelta(days=1)).isoformat()},
        {"status": "open", "departure_time": (NOW - timedelta(days=1)).isoformat()},
    ]
    report = run_seed(api)
    assert report.rides_created == 1 and len(api.rides[driver]) == 3


def test_works_with_secure_cookies_over_plain_http() -> None:
    """COOKIE_SECURE=true + http://: httpx würde das Original-Cookie nie zurücksenden."""
    api = FakeApi(secure_cookie=True)
    assert run_seed(api).rides_created == len(seed_demo.DEMO_RIDES)


def test_login_rate_limit_waits_and_retries() -> None:
    api = FakeApi()
    api.login_limit = 3
    slept: list[float] = []

    def fake_sleep(s: float) -> None:
        slept.append(s)
        api.login_limit = None  # Fenster abgelaufen

    report = run_seed(api, sleep=fake_sleep)
    assert slept == [3.0]
    assert report.rides_created == len(seed_demo.DEMO_RIDES)


def test_registration_failure_gives_a_clear_error() -> None:
    api = FakeApi()
    api.register_status = 429
    with pytest.raises(seed_demo.SeedError, match="Registrierung"):
        run_seed(api)


def test_register_422_names_the_likely_cause() -> None:
    api = FakeApi()
    api.register_status = 422
    with pytest.raises(seed_demo.SeedError, match=r"demo\.invalid"):
        run_seed(api)


def test_login_denied_after_registration_gives_a_clear_error() -> None:
    api = FakeApi()
    api.users[seed_demo.DEMO_DRIVERS[0].email] = {"password": "ein-anderes-passwort", "name": "x"}
    with pytest.raises(seed_demo.SeedError, match="anderen Passwort"):
        run_seed(api)


def test_failing_match_probe_is_only_a_hint() -> None:
    api = FakeApi()
    api.match_status = 503
    report = run_seed(api)
    assert report.match_probe is None and any("Kontrollsuche" in n for n in report.notes)


def test_unreachable_api_is_a_clear_error() -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    with pytest.raises(seed_demo.SeedError, match="nicht erreichbar"):
        seed_demo.seed("http://127.0.0.1:1", transport=httpx.MockTransport(refuse), now=NOW)


# --- main() ---------------------------------------------------------------------------------------


def test_main_refuses_production_without_any_request(capsys: pytest.CaptureFixture[str]) -> None:
    api = FakeApi()
    code = seed_demo.main(
        [], environ={"APP_ENV": "production"}, env_file=None, transport=api.transport()
    )
    assert code == 2 and api.requests == []
    assert "production" in capsys.readouterr().err


def test_main_refuses_remote_target_without_any_request() -> None:
    api = FakeApi()
    code = seed_demo.main(
        ["--base-url", "https://congress.example.org"],
        environ={},
        env_file=None,
        transport=api.transport(),
    )
    assert code == 2 and api.requests == []


def test_main_prints_credentials_and_sample_pickups(capsys: pytest.CaptureFixture[str]) -> None:
    api = FakeApi()
    code = seed_demo.main([], environ={}, env_file=None, transport=api.transport())
    out = capsys.readouterr().out
    assert code == 0
    assert seed_demo.DEMO_PASSWORD in out and seed_demo.DEMO_RIDER.email in out
    assert all(u.email in out for u in seed_demo.DEMO_DRIVERS)
    for name, (lat, lon) in seed_demo.SAMPLE_PICKUPS:
        assert name in out and f"lat={lat}" in out and f"lon={lon}" in out


def test_main_reports_errors_with_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    api = FakeApi()
    api.register_status = 429
    code = seed_demo.main([], environ={}, env_file=None, transport=api.transport())
    assert code == 1 and "Fehler:" in capsys.readouterr().err


# --- Koordinaten gegen echtes OSRM -------------------------------------------------------------


@pytest.mark.osrm
async def test_seed_coordinates_are_inside_the_hamburg_extract_and_routable() -> None:
    import os

    from app.config import get_settings
    from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError
    from tests.helpers import EVENT

    osrm = OsrmClient(os.environ.get("OSRM_URL", "http://127.0.0.1:55000"), timeout_s=5)
    try:
        try:
            await osrm.nearest(EVENT)
        except (RoutingUnavailableError, NoRouteError):
            pytest.skip("OSRM mit Hamburg-Extrakt nicht erreichbar")
        limit = get_settings().match_max_snap_distance_m
        points = [r.start for r in seed_demo.DEMO_RIDES] + [p for _, p in seed_demo.SAMPLE_PICKUPS]
        for point in points:
            assert (await osrm.nearest(point)).distance_m <= limit, point
        for ride in seed_demo.DEMO_RIDES:
            route = await osrm.route([ride.start, EVENT], geometry=False)
            assert 3_000 < route.distance_m < 60_000
    finally:
        await osrm.aclose()
