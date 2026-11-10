import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Deterministische Zeitzone für Datumsformatierung in den Tests.
process.env.TZ = "Europe/Berlin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Leaflet braucht echte Layoutmaße und Tile-Requests; in den Komponententests wird die Karte
// durch eine einfache Attrappe ersetzt (ein Test lädt die echte Karte über vi.importActual).
vi.mock("../components/MapView", async () => {
  const React = await import("react");
  return {
    MapView: function FakeMapView(props: {
      ariaLabel: string;
      markers?: { id: string; kind: string; label: string }[];
      routes?: { id: string; highlighted?: boolean; muted?: boolean }[];
      onPick?: (p: { lat: number; lon: number }) => void;
      tileUrl: string;
      attribution: string;
      maxZoom?: number;
      ref?: React.Ref<{ getCenter(): { lat: number; lon: number } }>;
    }) {
      React.useImperativeHandle(props.ref, () => ({ getCenter: () => ({ lat: 53.6, lon: 10.0 }) }), []);
      return React.createElement(
        "div",
        { role: "region", "aria-label": props.ariaLabel, "data-testid": "map", "data-tile-url": props.tileUrl, "data-attribution": props.attribution, "data-max-zoom": String(props.maxZoom ?? 19) },
        props.onPick
          ? React.createElement(
              "button",
              { type: "button", onClick: () => props.onPick?.({ lat: 53.55, lon: 9.99 }) },
              "Testklick auf die Karte",
            )
          : null,
        React.createElement(
          "ul",
          { "data-testid": "map-markers" },
          (props.markers ?? []).map((m) =>
            React.createElement("li", { key: m.id, "data-kind": m.kind }, m.label),
          ),
        ),
        React.createElement(
          "ul",
          { "data-testid": "map-routes" },
          (props.routes ?? []).map((r) =>
            React.createElement("li", {
              key: r.id,
              "data-route": r.id,
              "data-highlighted": String(r.highlighted === true),
              "data-muted": String(r.muted === true),
            }),
          ),
        ),
      );
    },
  };
});
