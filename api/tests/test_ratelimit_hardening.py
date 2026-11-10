"""Rate-Limiter: Speichergrenze, Fensterverwaltung, Gruppenfairness, IP-Normalisierung."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import Settings
from app.ratelimit import (
    OVERFLOW_FACTOR,
    OVERFLOW_GROUP_FACTOR,
    LimiterRegistry,
    SlidingWindowLimiter,
    client_ip,
    normalize_ip,
    overflow_group,
)


def test_eviction_respects_each_keys_own_window() -> None:
    lim = SlidingWindowLimiter(max_keys=3)
    assert lim.hit("registrierung", 1, 3600, now=0)[0]
    lim.hit("a", 5, 60, now=0)
    lim.hit("b", 5, 60, now=0)
    # Tabelle voll; bei t=100 sind a und b abgelaufen (60 s), die Registrierung (3600 s) nicht.
    lim.hit("c", 5, 60, now=100)
    assert "registrierung" in lim._hits
    assert "a" not in lim._hits and "b" not in lim._hits
    allowed, retry = lim.hit("registrierung", 1, 3600, now=101)
    assert not allowed and retry > 3000


def test_full_table_never_evicts_an_active_counter() -> None:
    lim = SlidingWindowLimiter(max_keys=20)
    for _ in range(3):
        assert lim.hit("opfer", 3, 60, now=0.0)[0]
    assert not lim.hit("opfer", 3, 60, now=0.5)[0]
    for i in range(500):  # Flut neuer Adressen: die Tabelle ist bald voll
        lim.hit(f"ip-{i}", 5, 60, now=1.0 + i / 1000)
    assert len(lim._hits) <= 20
    assert "opfer" in lim._hits
    allowed, retry = lim.hit("opfer", 3, 60, now=2.0)
    assert not allowed and retry > 50  # Sperre besteht weiter, sie wurde nicht zurückgesetzt


def test_full_table_new_keys_share_a_bucket_with_a_multiple_of_the_single_limit() -> None:
    lim = SlidingWindowLimiter(max_keys=3)
    for k in ("a", "b", "c"):
        assert lim.hit(k, 5, 60, now=0.0)[0]
    # Tabelle voll mit aktiven Schlüsseln: neue Schlüssel zählen im Überlauf. Der gemeinsame Eimer
    # fasst OVERFLOW_FACTOR × Limit – nicht mehr nur das Limit EINES Schlüssels.
    cap = 2 * OVERFLOW_FACTOR
    results = [lim.hit(f"neu-{i}", 2, 60, now=1.0)[0] for i in range(cap + 5)]
    assert results == [True] * cap + [False] * 5
    assert len(lim._hits) == 3  # die neuen Schlüssel haben nichts verdrängt
    assert len(lim._overflow) == cap
    # bekannte Schlüssel zählen weiter eigenständig
    assert lim.hit("a", 5, 60, now=2.0)[0]


def fill(lim: SlidingWindowLimiter, n: int, now: float = 0.0) -> None:
    for i in range(n):
        assert lim.hit(f"fuell-{i}", 100, 60, now=now)[0]


def test_normal_new_client_keeps_working_after_the_table_was_flooded() -> None:
    """Die Tabelle ist mit Wegwerf-Schlüsseln voll – ein normaler Neuer wird nicht gedrosselt."""
    lim = SlidingWindowLimiter(max_keys=100)
    fill(lim, 100)  # Angreifer füllt die Tabelle (jeder Schlüssel aktiv)
    for i in range(20):  # 20 normale neue Clients, jeder mit mehreren Zugriffen (Limit 10/min)
        for _ in range(8):
            allowed, _ = lim.hit(f"normal-{i}", 10, 60, now=1.0 + i, group=f"g-{i}")
            assert allowed
    assert lim.overflowed
    assert len(lim._hits) == 100


def test_one_attacker_group_cannot_exhaust_the_shared_overflow_bucket() -> None:
    lim = SlidingWindowLimiter(max_keys=10)
    fill(lim, 10)
    # ein Angreifer (eine IPv6-Zuweisung = eine Gruppe) feuert mit vielen /64-Schlüsseln
    sent = [lim.hit(f"v6-{i}", 4, 60, now=1.0, group="angreifer")[0] for i in range(1_000)]
    assert sum(sent) == 4 * OVERFLOW_GROUP_FACTOR  # nur sein Teil-Eimer, dann ist Schluss
    assert len(lim._overflow) == 4 * OVERFLOW_GROUP_FACTOR  # der gemeinsame Eimer ist kaum belegt
    # ein anderer Neuer (andere Gruppe) wird normal bedient
    assert lim.hit("normal", 4, 60, now=2.0, group="anderer")[0]


def test_overflow_is_still_finite_for_many_groups() -> None:
    lim = SlidingWindowLimiter(max_keys=10)
    fill(lim, 10)
    allowed = sum(lim.hit(f"k{i}", 2, 60, now=1.0, group=f"g{i % 40}")[0] for i in range(5_000))
    assert allowed == 2 * OVERFLOW_FACTOR  # der gemeinsame Eimer bleibt die harte Obergrenze
    allowed_later, retry = lim.hit("spaet", 2, 60, now=2.0, group="g-neu")
    assert not allowed_later and retry > 50


def test_overflow_memory_is_strictly_bounded() -> None:
    lim = SlidingWindowLimiter(max_keys=20, max_groups=8)
    fill(lim, 20)
    limit = 3
    for i in range(50_000):
        lim.hit(f"flut-{i}", limit, 60, now=1.0 + i / 100_000, group=f"g-{i}")
    assert len(lim._hits) <= 20
    assert len(lim._groups) <= 8
    assert len(lim._overflow) <= limit * OVERFLOW_FACTOR
    assert all(len(q) <= limit * OVERFLOW_GROUP_FACTOR for q in lim._groups.values())


def test_active_counters_survive_a_flood_in_the_overflow() -> None:
    lim = SlidingWindowLimiter(max_keys=10)
    for _ in range(3):
        assert lim.hit("opfer", 3, 60, now=0.0)[0]
    fill(lim, 9)
    for i in range(5_000):
        lim.hit(f"flut-{i}", 3, 60, now=1.0, group=f"g{i % 50}")
    allowed, retry = lim.hit("opfer", 3, 60, now=2.0)
    assert not allowed and retry > 50  # die Sperre blieb bestehen
    assert "opfer" in lim._hits


def test_overflow_recovers_when_the_table_frees_up() -> None:
    lim = SlidingWindowLimiter(max_keys=2)
    lim.hit("a", 5, 60, now=0.0)
    lim.hit("b", 5, 60, now=0.0)
    cap = OVERFLOW_FACTOR  # Limit 1
    assert all(lim.hit(f"x{i}", 1, 60, now=1.0)[0] for i in range(cap))
    assert not lim.hit("y", 1, 60, now=2.0)[0]  # gemeinsamer Eimer erschöpft
    # nach Ablauf der Fenster ist alles wieder frei und "y" bekommt einen eigenen Zähler
    assert lim.hit("y", 1, 60, now=100.0)[0]
    assert "y" in lim._hits and not lim.overflowed
    assert not lim._groups  # abgelaufene Gruppen-Eimer sind weg


def test_overflow_warning_names_the_table_but_no_client(caplog: pytest.LogCaptureFixture) -> None:
    reg = LimiterRegistry(max_keys=2)
    lim = reg.get("register")
    lim.hit("a", 5, 60, now=0.0)
    lim.hit("b", 5, 60, now=0.0)
    with caplog.at_level("WARNING", logger="app.ratelimit"):
        for _ in range(5):
            lim.hit("203.0.113.66-geheim", 5, 60, now=1.0, group="2001:db8::/48")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # einmal je Überlauf-Episode, kein Log-Flut-Hebel
    assert "register" in warnings[0].getMessage()
    assert "203.0.113" not in caplog.text and "2001:db8" not in caplog.text


# --- reserve / release ---------------------------------------------------------------------------


def test_release_gives_the_slot_back_and_forgets_empty_keys() -> None:
    lim = SlidingWindowLimiter(max_keys=5)
    token, _ = lim.reserve("k", 1, 60, now=0.0)
    assert token is not None
    assert lim.reserve("k", 1, 60, now=1.0)[0] is None  # belegt
    lim.release(token)
    assert "k" not in lim._hits and "k" not in lim._expiry  # keine leeren Reste
    assert lim.reserve("k", 1, 60, now=2.0)[0] is not None


def test_release_keeps_the_other_slots_of_the_same_key() -> None:
    lim = SlidingWindowLimiter(max_keys=5)
    first, _ = lim.reserve("k", 3, 60, now=0.0)
    second, _ = lim.reserve("k", 3, 60, now=1.0)
    assert first is not None and second is not None
    lim.release(second)
    assert list(lim._hits["k"]) == [0.0]


def test_release_then_sweep_on_a_full_table_does_not_raise() -> None:
    lim = SlidingWindowLimiter(max_keys=3)
    token, _ = lim.reserve("a", 5, 60, now=0.0)
    assert token is not None
    lim.release(token)
    for k in ("b", "c", "d"):
        lim.hit(k, 5, 60, now=0.0)
    lim.hit("e", 5, 60, now=100.0)  # voll → Sweep nach Ablauf aller Fenster: darf nicht werfen
    assert "e" in lim._hits


def test_release_in_the_overflow_takes_the_slot_out_of_both_buckets() -> None:
    lim = SlidingWindowLimiter(max_keys=1)
    lim.hit("a", 5, 60, now=0.0)
    token, _ = lim.reserve("neu", 1, 60, now=1.0, group="g")
    assert token is not None
    assert len(lim._overflow) == 1 and len(lim._groups["g"]) == 1
    lim.release(token)
    assert not lim._overflow and not lim._groups["g"]


def test_memory_stays_bounded_with_many_keys() -> None:
    lim = SlidingWindowLimiter(max_keys=50)
    for i in range(2_000):
        lim.hit(f"k{i}", 5, 60, now=i / 100)
    assert len(lim._hits) <= 50
    assert set(lim._expiry) <= set(lim._hits)


def test_registry_keeps_one_independent_limiter_per_name() -> None:
    reg = LimiterRegistry(max_keys=5)
    assert reg.get("login") is reg.get("login")
    assert reg.get("login") is not reg.get("api")
    for _ in range(3):
        assert reg.hit("login", "opfer", 3, 60, now=0.0)[0]
    for i in range(200):  # Flut im Namen "api" kann den Namen "login" nicht berühren
        reg.hit("api", f"ip-{i}", 5, 60, now=1.0 + i / 1000)
    assert len(reg.get("api")._hits) <= 5
    assert not reg.hit("login", "opfer", 3, 60, now=2.0)[0]
    assert reg.get("login").overflowed is False
    assert reg.get("api").overflowed is True


def test_clock_is_injectable() -> None:
    t = [100.0]
    lim = SlidingWindowLimiter(clock=lambda: t[0])
    assert lim.hit("k", 1, 10)[0]
    assert not lim.hit("k", 1, 10)[0]
    t[0] = 110.5
    assert lim.hit("k", 1, 10)[0]


def test_reset_clears_everything() -> None:
    reg = LimiterRegistry(max_keys=1)
    reg.hit("g", "k", 1, 10, now=0)
    reg.hit("g", "l", 1, 10, now=0)  # läuft in den Überlauf-Eimer
    reg.reset()
    inst = reg.get("g")
    assert not inst._hits and not inst._expiry and not inst._overflow and not inst.overflowed
    assert reg.hit("g", "k", 1, 10, now=0)[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("203.0.113.9:51234", "203.0.113.9"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("[2001:db8:1:2::1]:443", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("fe80::1%eth0", "fe80::/64"),
        ("::1", "::/64"),
        ("unknown", "unknown"),
        ("", "unknown"),
    ],
)
def test_normalize_ip(raw: str, expected: str) -> None:
    assert normalize_ip(raw) == expected


def test_overflow_group_is_a_48_for_ipv6_and_the_address_for_ipv4() -> None:
    assert overflow_group("n", "2001:db8:1:2::1") == overflow_group("n", "2001:db8:1:ffff::9")
    assert overflow_group("n", "2001:db8:1:2::1") != overflow_group("n", "2001:db8:2:2::1")
    assert overflow_group("n", "192.0.2.1") != overflow_group("n", "192.0.2.2")
    assert overflow_group("n", "::ffff:192.0.2.1") == overflow_group("n", "192.0.2.1")
    assert overflow_group("n", "192.0.2.1") != overflow_group("m", "192.0.2.1")
    assert "192.0.2.1" not in overflow_group("n", "192.0.2.1")  # nur Salt-Hash
    assert overflow_group("n", "kein-ip")  # Rohtext statt Fehler


def test_normalize_ip_truncates_garbage() -> None:
    assert len(normalize_ip("x" * 500)) <= 64


def _request(peer: str | None, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", f.encode()) for f in forwarded]
    scope = {"type": "http", "client": (peer, 1234) if peer else None, "headers": headers}
    return Request(scope)


TRUSTED = Settings(trusted_proxies="10.0.0.0/8, 2001:db8:ffff::/48")


def test_client_ip_without_peer_is_unknown() -> None:
    assert client_ip(_request(None), TRUSTED) == "unknown"


def test_client_ip_joins_multiple_forwarded_header_lines() -> None:
    # Die vom Proxy angehängte Zeile steht hinten und gilt.
    assert client_ip(_request("10.0.0.5", "6.6.6.6", "198.51.100.7"), TRUSTED) == "198.51.100.7"


def test_client_ip_walks_back_over_trusted_hops() -> None:
    req = _request("10.0.0.5", "6.6.6.6, 198.51.100.7, 10.0.0.9")
    assert client_ip(req, TRUSTED) == "198.51.100.7"


def test_client_ip_ipv4_mapped_trusted_peer() -> None:
    assert client_ip(_request("::ffff:10.0.0.5", "198.51.100.7"), TRUSTED) == "198.51.100.7"


def test_client_ip_ipv6_trusted_proxy() -> None:
    peer = "2001:db8:ffff::1"
    assert client_ip(_request(peer, "2001:db8:1::5"), TRUSTED) == "2001:db8:1::5"


def test_client_ip_forwarded_entry_with_port_is_understood() -> None:
    assert client_ip(_request("10.0.0.5", "198.51.100.7:4711"), TRUSTED) == "198.51.100.7"


def test_client_ip_garbage_or_empty_forwarded_falls_back_to_peer() -> None:
    assert client_ip(_request("10.0.0.5", "kein-ip"), TRUSTED) == "10.0.0.5"
    assert client_ip(_request("10.0.0.5"), TRUSTED) == "10.0.0.5"
    assert client_ip(_request("10.0.0.5", ""), TRUSTED) == "10.0.0.5"


def test_client_ip_all_entries_trusted_falls_back_to_peer() -> None:
    assert client_ip(_request("10.0.0.5", "10.1.1.1, 10.2.2.2"), TRUSTED) == "10.0.0.5"


def test_client_ip_untrusted_peer_ignores_header() -> None:
    assert client_ip(_request("203.0.113.9", "198.51.100.7"), TRUSTED) == "203.0.113.9"
