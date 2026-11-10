"""Login-Limits je E-Mail: nur Fehlversuche zählen (kein gezieltes Aussperren), über die echte App.

Alle ASGI-Anfragen haben denselben Peer; verschiedene Clients entstehen durch unterschiedliche
``X-Forwarded-For``-Werte (``trusted_proxies`` = Peer), wie hinter Caddy.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.auth.passwords import Argon2PasswordHasher
from tests.helpers import create_user
from tests.test_ratelimit_app import PASSWORD, assert_limited, limits, xff

pytestmark = pytest.mark.asyncio(loop_scope="session")

LOGIN = "/api/auth/login"
WRONG = "falsch-falsch-falsch"
VICTIM = "opfer@example.org"


async def attempt(client: httpx.AsyncClient, email: str, password: str, ip: str) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": password}, headers=xff(ip))


@pytest.fixture
async def victim(client: httpx.AsyncClient, db: Any) -> dict[str, Any]:
    return await create_user(db, email=VICTIM, password_hash=Argon2PasswordHasher().hash(PASSWORD))


# --- (1) Ein Angreifer sperrt den legitimen Login des Opfers NICHT aus --------------------------


async def test_attacker_with_wrong_passwords_does_not_lock_out_the_victim(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(
        rate_limit_login_fail_per_email_ip_per_min=3, rate_limit_login_fail_per_email_per_min=30
    ):
        codes = [
            (await attempt(client, VICTIM, WRONG, "203.0.113.66")).status_code for _ in range(8)
        ]
        assert codes == [401, 401, 401, 429, 429, 429, 429, 429]
        # das Opfer meldet sich von seiner Adresse mit richtigem Passwort an – trotz Dauerfeuer
        ok = await attempt(client, VICTIM, PASSWORD, "198.51.100.7")
        assert ok.status_code == 200, ok.text
        assert "set-cookie" in ok.headers
        # auch mit Tippfehlern: je E-Mail+IP sind 3 Fehlversuche frei
        assert (await attempt(client, VICTIM, WRONG, "198.51.100.7")).status_code == 401


async def test_attacker_on_one_ip_cannot_fill_the_global_counter(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    """Der enge Zähler (E-Mail+IP) weist den Angreifer ab, bevor der weite zählt."""
    with limits(
        rate_limit_login_fail_per_email_ip_per_min=2, rate_limit_login_fail_per_email_per_min=4
    ):
        for _ in range(50):
            await attempt(client, VICTIM, WRONG, "203.0.113.66")
        # global sind erst 2 von 4 Fehlversuchen belegt: das Opfer hat noch Luft
        for i in range(2):
            assert (await attempt(client, VICTIM, PASSWORD, f"198.51.100.{i}")).status_code == 200


async def test_successful_logins_are_not_counted(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(
        rate_limit_login_fail_per_email_ip_per_min=2, rate_limit_login_fail_per_email_per_min=2
    ):
        for _ in range(6):
            assert (await attempt(client, VICTIM, PASSWORD, "198.51.100.7")).status_code == 200
        # und sie haben keinen Platz belegt: zwei Fehlversuche sind weiter frei
        assert (await attempt(client, VICTIM, WRONG, "198.51.100.7")).status_code == 401
        assert (await attempt(client, VICTIM, WRONG, "198.51.100.7")).status_code == 401
        assert (await attempt(client, VICTIM, WRONG, "198.51.100.7")).status_code == 429


# --- (2) Brute-Force wird weiter gebremst --------------------------------------------------------


async def test_brute_force_from_one_address_is_throttled_with_retry_after(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(rate_limit_login_fail_per_email_ip_per_min=3):
        for i in range(3):
            assert (
                await attempt(client, VICTIM, f"raten-{i}-xxxxxx", "203.0.113.66")
            ).status_code == 401
        blocked = await attempt(client, VICTIM, "raten-3-xxxxxx", "203.0.113.66")
        assert_limited(blocked)
        # Kernpunkt: auch das RICHTIGE Passwort kommt dort nicht durch – sonst liefe 429/200 als
        # Orakel, und der Angreifer könnte unbegrenzt weiterraten
        assert_limited(await attempt(client, VICTIM, PASSWORD, "203.0.113.66"))
        assert "set-cookie" not in blocked.headers


async def test_distributed_brute_force_hits_the_global_per_email_limit(
    client: httpx.AsyncClient, victim: dict[str, Any], db: Any
) -> None:
    await create_user(
        db, email="anderer@example.org", password_hash=Argon2PasswordHasher().hash(PASSWORD)
    )
    with limits(
        rate_limit_login_fail_per_email_ip_per_min=3, rate_limit_login_fail_per_email_per_min=4
    ):
        codes = [
            (await attempt(client, VICTIM, WRONG, f"203.0.113.{10 + i}")).status_code
            for i in range(6)
        ]
        assert codes == [401, 401, 401, 401, 429, 429]  # jede Adresse nur einmal – global 4
        blocked = await attempt(client, VICTIM, PASSWORD, "198.51.100.7")
        assert_limited(
            blocked
        )  # dokumentierte Grenze: erst das globale Limit sperrt auch das Opfer
        # andere Konten sind nicht betroffen
        assert (
            await attempt(client, "anderer@example.org", PASSWORD, "198.51.100.7")
        ).status_code == 200


async def test_per_ip_login_limit_still_counts_every_attempt(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(rate_limit_login_per_min=3):
        codes = [
            (await attempt(client, VICTIM, PASSWORD, "198.51.100.7")).status_code for _ in range(4)
        ]
        assert codes == [200, 200, 200, 429]


async def test_parallel_guesses_cannot_exceed_the_limit(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    """Plätze werden vor der Hash-Prüfung belegt: Parallelität überschießt das Limit nicht."""
    with limits(rate_limit_login_fail_per_email_ip_per_min=3, rate_limit_login_per_min=1000):
        responses = await asyncio.gather(
            *(attempt(client, VICTIM, f"parallel-{i}-xxxx", "203.0.113.66") for i in range(12))
        )
        codes = sorted(r.status_code for r in responses)
        assert codes == [401] * 3 + [429] * 9


# --- (3) Keine Nutzerenumeration ----------------------------------------------------------------


async def test_limits_give_no_account_oracle(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(
        rate_limit_login_fail_per_email_ip_per_min=2, rate_limit_login_fail_per_email_per_min=3
    ):
        seen: dict[str, list[tuple[int, Any]]] = {}
        for email in (VICTIM, "unbekannt@example.org"):
            rows = []
            # erst eine Adresse (E-Mail+IP-Limit), dann weitere Adressen (globales Limit)
            for ip in ("203.0.113.1", "203.0.113.1", "203.0.113.1", "203.0.113.2", "203.0.113.3"):
                r = await attempt(client, email, WRONG, ip)
                rows.append((r.status_code, r.json()))
            # ... und zuletzt das (für das echte Konto richtige) Passwort
            r = await attempt(client, email, PASSWORD, "203.0.113.9")
            rows.append((r.status_code, r.json()))
            seen[email] = rows
        assert [c for c, _ in seen[VICTIM]] == [401, 401, 429, 401, 429, 429]
        assert seen[VICTIM] == seen["unbekannt@example.org"]  # Status UND Fehlertexte identisch


async def test_email_spelling_does_not_evade_the_limits(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(rate_limit_login_fail_per_email_ip_per_min=2):
        spelled = [VICTIM, " OPFER@example.org", "Opfer@Example.ORG ", VICTIM]
        codes = [(await attempt(client, e, WRONG, "203.0.113.66")).status_code for e in spelled]
        assert codes == [401, 401, 429, 429]


async def test_rate_limit_disabled_skips_the_failure_counters(
    client: httpx.AsyncClient, victim: dict[str, Any]
) -> None:
    with limits(rate_limit_enabled=False, rate_limit_login_fail_per_email_ip_per_min=1):
        codes = [
            (await attempt(client, VICTIM, WRONG, "203.0.113.66")).status_code for _ in range(4)
        ]
        assert codes == [401] * 4


# --- Überlauf über die echte App ------------------------------------------------------------------


async def test_new_clients_can_still_register_when_the_table_is_flooded(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tabelle mit fremden Adressen gefüllt: ein normaler neuer Client registriert sich weiter."""
    from app import ratelimit

    small = ratelimit.LimiterRegistry(max_keys=5)
    monkeypatch.setattr(ratelimit, "limiter", small)

    def register(ip: str, n: int) -> Any:
        return client.post(
            "/api/auth/register",
            json={
                "email": f"neu{n}@example.org",
                "password": PASSWORD,
                "display_name": "Neu",
                "consent": True,
            },
            headers=xff(ip),
        )

    with limits(rate_limit_register_per_hour=2):
        for i in range(5):  # Angreifer füllt die Registrierungs-Tabelle mit fünf Adressen
            assert (await register(f"203.0.113.{i}", i)).status_code == 202
        assert small.get("register").overflowed is False
        # ab jetzt ist die Tabelle voll: neue Adressen liefen früher in EINEN Eimer mit Limit 2
        codes = [(await register(f"198.51.100.{i}", 100 + i)).status_code for i in range(8)]
        assert small.get("register").overflowed is True
        assert codes == [202] * 8
        # dieselbe neue Adresse bleibt trotzdem begrenzt (Gruppen-Eimer = Vielfaches des Limits)
        spam = [(await register("198.51.100.200", 200 + i)).status_code for i in range(20)]
        assert spam.count(202) == 2 * ratelimit.OVERFLOW_GROUP_FACTOR
        assert spam[-1] == 429


async def test_login_failures_with_random_emails_do_not_grow_memory_beyond_the_table(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import ratelimit

    small = ratelimit.LimiterRegistry(max_keys=10)
    monkeypatch.setattr(ratelimit, "limiter", small)
    with limits():
        for i in range(60):
            await attempt(client, f"zufall{i}@example.org", WRONG, f"203.0.113.{i}")
    for name in ("login_fail_email_ip", "login_fail_email"):
        assert len(small.get(name)._hits) <= 10
