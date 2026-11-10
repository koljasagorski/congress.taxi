"""Rate-Limiter: Speichergrenze, Fensterverwaltung, Gruppenfairness, IP-Normalisierung."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import Settings
from app.ratelimit import (
    LimiterRegistry,
    SlidingWindowLimiter,
    client_ip,
    normalize_ip,
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


def test_full_table_is_fail_closed_for_new_keys() -> None:
    lim = SlidingWindowLimiter(max_keys=3)
    for k in ("a", "b", "c"):
        assert lim.hit(k, 5, 60, now=0.0)[0]
    # Tabelle voll mit aktiven Schlüsseln: neue Schlüssel teilen sich EINEN Eimer mit dem Limit
    results = [lim.hit(f"neu-{i}", 2, 60, now=1.0)[0] for i in range(5)]
    assert results == [True, True, False, False, False]
    assert len(lim._hits) == 3  # die neuen Schlüssel haben nichts verdrängt
    # bekannte Schlüssel zählen weiter eigenständig
    assert lim.hit("a", 5, 60, now=2.0)[0]


def test_overflow_bucket_recovers_when_the_table_frees_up() -> None:
    lim = SlidingWindowLimiter(max_keys=2)
    lim.hit("a", 5, 60, now=0.0)
    lim.hit("b", 5, 60, now=0.0)
    assert lim.hit("x", 1, 60, now=1.0)[0]
    assert not lim.hit("y", 1, 60, now=2.0)[0]  # gemeinsamer Eimer erschöpft
    # nach Ablauf der Fenster ist alles wieder frei und "y" bekommt einen eigenen Zähler
    assert lim.hit("y", 1, 60, now=100.0)[0]
    assert "y" in lim._hits and not lim.overflowed


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
