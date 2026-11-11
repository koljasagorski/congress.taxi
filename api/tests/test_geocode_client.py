"""Nominatim-Client: Anfrage, Antwort, Cache, 1-Anfrage-pro-Sekunde-Drossel, Fehlerpfade."""

from __future__ import annotations

import httpx
import pytest

from app.geocoding import (
    GeocoderBusyError,
    GeocoderUnavailableError,
    NominatimClient,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

HAMBURG = {"lat": "53.5615", "lon": "9.9885", "display_name": "Messe Hamburg, Karolinenviertel"}


class Clock:
    """Steuerbare Uhr samt ``sleep``, damit die Drossel ohne echte Wartezeit testbar ist."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(
    handler: httpx.MockTransport | None = None,
    *,
    clock: Clock | None = None,
    body: object | None = None,
    status: int = 200,
    seen: list[httpx.Request] | None = None,
    **kwargs: object,
) -> NominatimClient:
    clock = clock or Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=[HAMBURG] if body is None else body)

    return NominatimClient(
        "https://nominatim.example.test",
        timeout_s=2.0,
        user_agent="test-agent/1.0",
        transport=handler or httpx.MockTransport(respond),
        clock=clock,
        sleep=clock.sleep,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_search_parses_results_and_sends_expected_request() -> None:
    seen: list[httpx.Request] = []
    client = make_client(seen=seen)
    hits = await client.search("Alexanderplatz 1")
    assert [(h.label, h.lat, h.lon) for h in hits] == [
        ("Messe Hamburg, Karolinenviertel", 53.5615, 9.9885)
    ]
    (request,) = seen
    assert request.url.path == "/search"
    params = dict(request.url.params)
    assert params["q"] == "Alexanderplatz 1"
    assert params["format"] == "jsonv2"
    assert params["countrycodes"] == "de"
    assert params["accept-language"] == "de"
    assert params["limit"] == "5"
    assert request.headers["user-agent"] == "test-agent/1.0"
    # keine Cookies/Referer/Autorisierung an den Dritten
    assert "cookie" not in request.headers
    assert "referer" not in request.headers
    await client.aclose()


async def test_empty_result_is_empty_list() -> None:
    client = make_client(body=[])
    assert await client.search("Nirgendwo") == []
    await client.aclose()


async def test_malformed_entries_are_skipped() -> None:
    body = [
        HAMBURG,
        {"lat": "abc", "lon": "9.9", "display_name": "kaputt"},
        {"lat": "95", "lon": "9.9", "display_name": "ausserhalb"},
        {"lat": "nan", "lon": "9.9", "display_name": "nan"},
        {"lon": "9.9", "display_name": "ohne lat"},
        {"lat": "53.5", "lon": "9.9"},
        "kein objekt",
    ]
    client = make_client(body=body)
    hits = await client.search("irgendwas")
    assert [h.label for h in hits] == ["Messe Hamburg, Karolinenviertel"]
    await client.aclose()


async def test_results_are_capped_and_labels_truncated() -> None:
    body = [{"lat": "53.5", "lon": "9.9", "display_name": "X" * 500} for _ in range(12)]
    client = make_client(body=body)
    hits = await client.search("viele")
    assert len(hits) == 5
    assert all(len(h.label) <= 200 for h in hits)
    await client.aclose()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_upstream_errors_are_unavailable(status: int) -> None:
    client = make_client(status=status, body=[])
    with pytest.raises(GeocoderUnavailableError):
        await client.search("Fehler")
    await client.aclose()


async def test_network_error_is_unavailable() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    client = make_client(httpx.MockTransport(boom))
    with pytest.raises(GeocoderUnavailableError):
        await client.search("Netz")
    await client.aclose()


@pytest.mark.parametrize("payload", [{"a": 1}, "text", 42])
async def test_unexpected_json_is_unavailable(payload: object) -> None:
    client = make_client(body=payload)
    with pytest.raises(GeocoderUnavailableError):
        await client.search("Form")
    await client.aclose()


async def test_not_json_is_unavailable() -> None:
    client = make_client(httpx.MockTransport(lambda _: httpx.Response(200, text="<html>")))
    with pytest.raises(GeocoderUnavailableError):
        await client.search("html")
    await client.aclose()


async def test_cache_hits_do_not_call_upstream_and_ignore_case_and_spacing() -> None:
    seen: list[httpx.Request] = []
    client = make_client(seen=seen)
    first = await client.search("Marktstraße  5, Hamburg")
    second = await client.search("  marktstraße 5,   HAMBURG ")
    assert first == second
    assert len(seen) == 1
    await client.aclose()


async def test_cache_is_not_shared_between_users() -> None:
    seen: list[httpx.Request] = []
    client = make_client(seen=seen)
    await client.search("Gleiche Adresse 1", scope="nutzer-a")
    await client.search("Gleiche Adresse 1", scope="nutzer-b")  # fremder Cache darf nicht greifen
    await client.search("Gleiche Adresse 1", scope="nutzer-a")  # eigener Cache greift
    assert len(seen) == 2
    await client.aclose()


async def test_cache_expires() -> None:
    clock = Clock()
    seen: list[httpx.Request] = []
    client = make_client(clock=clock, seen=seen, cache_ttl_s=60.0)
    await client.search("Ablauf")
    clock.now += 61
    await client.search("Ablauf")
    assert len(seen) == 2
    await client.aclose()


async def test_errors_are_not_cached() -> None:
    calls = 0

    def flaky(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json=[])
        return httpx.Response(200, json=[HAMBURG])

    client = make_client(httpx.MockTransport(flaky))
    with pytest.raises(GeocoderUnavailableError):
        await client.search("Wackel")
    assert len(await client.search("Wackel")) == 1
    await client.aclose()


async def test_empty_results_are_cached() -> None:
    seen: list[httpx.Request] = []
    client = make_client(body=[], seen=seen)
    await client.search("leer")
    await client.search("leer")
    assert len(seen) == 1
    await client.aclose()


async def test_throttle_spaces_distinct_queries_by_one_second() -> None:
    clock = Clock()
    client = make_client(clock=clock)
    await client.search("eins")  # erste Anfrage ohne Wartezeit
    await client.search("zwei")  # sofort danach: eine Sekunde Abstand
    assert clock.slept == [pytest.approx(1.0)]
    await client.aclose()


async def test_no_wait_after_the_interval_has_passed() -> None:
    clock = Clock()
    client = make_client(clock=clock)
    await client.search("eins")
    clock.now += 5
    await client.search("zwei")
    assert clock.slept == []
    await client.aclose()


async def test_busy_when_the_queue_is_too_long() -> None:
    clock = Clock()
    client = make_client(clock=clock, max_wait_s=2.5)
    # drei Anfragen "gleichzeitig" belegen die Plätze 0, 1 und 2 s; die vierte (3 s) ist zu spät
    for _ in range(3):
        client._reserve_slot()
    with pytest.raises(GeocoderBusyError):
        await client.search("vier")
    await client.aclose()
