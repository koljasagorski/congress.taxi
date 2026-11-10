import { fireEvent, render } from "@testing-library/react";
import L from "leaflet";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import type { MapViewHandle } from "./MapView";

// Die globale Attrappe (test/setup.ts) wird hier umgangen: echte Leaflet-Karte in jsdom.
const { MapView } = await vi.importActual<typeof import("./MapView")>("./MapView");

const ATTRIBUTION =
  '&copy; <a href="https://tiles.example.test/copyright">Beispielkarte</a>-Mitwirkende';

describe("MapView (echtes Leaflet in jsdom)", () => {
  it("baut Karte, Kacheln, lokale Marker-Icons und Attribution auf", () => {
    const ref = createRef<MapViewHandle>();
    const { container } = render(
      <MapView
        ref={ref}
        tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION}
        center={{ lat: 53.55, lon: 9.99 }}
        markers={[{ id: "a", position: { lat: 53.55, lon: 9.99 }, label: "Startpunkt", kind: "start" }]}
        routes={[{ id: "r", points: [[53.55, 9.99], [53.56, 9.98]], label: "Route" }]}
        ariaLabel="Karte"
        showCrosshair
      />,
    );
    expect(container.querySelector(".leaflet-container")).not.toBeNull();
    expect(container.querySelector(".leaflet-marker-icon")).not.toBeNull();
    expect(container.querySelector("path.leaflet-interactive, svg path")).not.toBeNull();
    // Marker-Icon stammt aus dem Bundle, nicht von einem CDN
    const src = container.querySelector<HTMLImageElement>(".leaflet-marker-icon")?.getAttribute("src") ?? "";
    expect(src).not.toMatch(/^https?:\/\//);
    expect(container.querySelector(".leaflet-control-attribution")?.textContent).toContain("Beispielkarte");
    const link = container.querySelector<HTMLAnchorElement>(".leaflet-control-attribution a[href*='tiles.example.test']");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    const center = ref.current?.getCenter();
    expect(center?.lat).toBeCloseTo(53.55, 4);
    expect(center?.lon).toBeCloseTo(9.99, 4);
  });

  it("setzt per Klick auf die Karte einen Punkt (onPick mit gerundeten Koordinaten)", () => {
    const onPick = vi.fn();
    const { container } = render(
      <MapView
        tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION}
        center={{ lat: 53.55, lon: 9.99 }}
        onPick={onPick}
        ariaLabel="Karte"
      />,
    );
    const map = container.querySelector(".leaflet-container");
    expect(map).not.toBeNull();
    if (!map) return;
    fireEvent.mouseDown(map, { clientX: 0, clientY: 0 });
    fireEvent.mouseUp(map, { clientX: 0, clientY: 0 });
    fireEvent.click(map, { clientX: 0, clientY: 0 });
    expect(onPick).toHaveBeenCalledTimes(1);
    const point = onPick.mock.calls[0]?.[0] as { lat: number; lon: number };
    expect(point.lat).toBeCloseTo(53.55, 2);
    expect(point.lon).toBeCloseTo(9.99, 2);
    // höchstens fünf Nachkommastellen
    expect(String(point.lat).split(".")[1]?.length ?? 0).toBeLessThanOrEqual(5);
  });

  it("zeichnet beim Neu-Rendern nur die aktuellen Marker (keine Anhäufung)", () => {
    const props = {
      tileUrl: "https://tiles.example.test/{z}/{x}/{y}.png",
      attribution: ATTRIBUTION,
      center: { lat: 53.55, lon: 9.99 },
      ariaLabel: "Karte",
    };
    const first = [{ id: "a", position: { lat: 53.55, lon: 9.99 }, label: "Start", kind: "start" as const }];
    const second = [{ id: "b", position: { lat: 53.56, lon: 9.98 }, label: "Abholung", kind: "pickup" as const }];
    const { container, rerender } = render(<MapView {...props} markers={first} />);
    expect(container.querySelectorAll(".leaflet-marker-icon")).toHaveLength(1);
    rerender(<MapView {...props} markers={second} />);
    const icons = container.querySelectorAll(".leaflet-marker-icon");
    expect(icons).toHaveLength(1);
    expect(icons[0]?.getAttribute("alt")).toBe("Abholung");
    rerender(<MapView {...props} markers={[]} routes={[]} />);
    expect(container.querySelectorAll(".leaflet-marker-icon")).toHaveLength(0);
  });

  it("ist per Tastatur bedienbar (Tab-Fokus auf der Karte)", () => {
    const { container } = render(
      <MapView tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION} center={{ lat: 53.55, lon: 9.99 }} ariaLabel="Karte" />,
    );
    expect(container.querySelector(".leaflet-container")?.getAttribute("tabindex")).toBe("0");
  });

  it("lädt Kacheln nur mit strict-origin als Referrer-Policy und begrenzt den Zoom auf Wunsch", () => {
    const spy = vi.spyOn(L, "tileLayer");
    render(
      <MapView
        tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION}
        center={{ lat: 53.55, lon: 9.99 }}
        zoom={16}
        maxZoom={16}
        ariaLabel="Karte"
      />,
    );
    const options = spy.mock.calls[0]?.[1];
    expect(options?.referrerPolicy).toBe("strict-origin");
    expect(options?.maxZoom).toBe(16);
  });

  it("deckelt den Zoom der Karte selbst: Hineinzoomen ist am Limit gesperrt", () => {
    const { container } = render(
      <MapView
        tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION}
        center={{ lat: 53.55, lon: 9.99 }}
        zoom={16}
        maxZoom={16}
        ariaLabel="Karte"
      />,
    );
    expect(container.querySelector(".leaflet-control-zoom-in")?.classList.contains("leaflet-disabled")).toBe(true);
  });

  it("nennt unter der Karte den Kartenserver und was an ihn übertragen wird", () => {
    const { container } = render(
      <MapView
        tileUrl="https://tiles.example.test/{z}/{x}/{y}.png"
        attribution={ATTRIBUTION}
        center={{ lat: 53.55, lon: 9.99 }}
        ariaLabel="Karte"
      />,
    );
    const notice = container.querySelector(".map-wrap .tiles-notice")?.textContent ?? "";
    expect(notice).toContain("tiles.example.test");
    expect(notice).toContain("IP-Adresse");
    expect(notice).toContain("Kartenausschnitt");
  });
});
