import L from "leaflet";
import "leaflet/dist/leaflet.css";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";
import { useEffect, useId, useImperativeHandle, useRef, type Ref } from "react";
import type { LatLon, RoutePoints } from "../api/types";
import { useI18n } from "../i18n";
import { safeAttributionHtml } from "../lib/attribution";
import { hostOf } from "../lib/format";

export interface MapMarker {
  id: string;
  position: LatLon;
  label: string;
  kind: "start" | "pickup" | "event";
}

export interface MapRoute {
  id: string;
  points: RoutePoints;
  label: string;
  highlighted?: boolean;
  muted?: boolean;
}

export interface MapViewHandle {
  getCenter(): LatLon;
}

export interface MapViewProps {
  tileUrl: string;
  /** Quellenangabe aus GET /api/config (map.attribution); wird vor der Anzeige bereinigt. */
  attribution: string;
  center: LatLon;
  zoom?: number;
  /** Größte Zoomstufe (Standard 19). Je tiefer gezoomt wird, desto genauer verraten die Kachelabrufe den Ausschnitt. */
  maxZoom?: number;
  markers?: MapMarker[];
  routes?: MapRoute[];
  /** Klick auf die Karte setzt einen Punkt. */
  onPick?: (point: LatLon) => void;
  /** Ändert sich dieser Wert, passt die Karte den Ausschnitt an Marker und Routen an. */
  fitSignature?: string;
  ariaLabel: string;
  showCrosshair?: boolean;
  ref?: Ref<MapViewHandle>;
}

// Marker-Icons kommen aus dem Bundle (Vite-Assets), nie von einem CDN.
function makeIcon(kind: MapMarker["kind"]): L.Icon {
  return L.icon({
    iconUrl,
    iconRetinaUrl,
    shadowUrl,
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    tooltipAnchor: [16, -28],
    shadowSize: [41, 41],
    className: `marker-${kind}`,
  });
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function round5(n: number): number {
  return Math.round(n * 1e5) / 1e5;
}

export function MapView({
  tileUrl,
  attribution,
  center,
  zoom = 11,
  maxZoom = 19,
  markers = [],
  routes = [],
  onPick,
  fitSignature,
  ariaLabel,
  showCrosshair = false,
  ref,
}: MapViewProps) {
  const { t } = useI18n();
  const hintId = useId();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const onPickRef = useRef(onPick);
  const initialView = useRef({ center, zoom });

  useEffect(() => {
    onPickRef.current = onPick;
  }, [onPick]);

  useImperativeHandle(
    ref,
    () => ({
      getCenter: () => {
        const c = mapRef.current?.getCenter();
        return c ? { lat: round5(c.lat), lon: round5(c.lng) } : initialView.current.center;
      },
    }),
    [],
  );

  // Karte einmalig anlegen (bzw. neu, wenn sich die Tile-URL ändert).
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const reduced = prefersReducedMotion();
    const map = L.map(container, {
      center: [initialView.current.center.lat, initialView.current.center.lon],
      zoom: initialView.current.zoom,
      maxZoom,
      zoomAnimation: !reduced,
      fadeAnimation: !reduced,
      markerZoomAnimation: !reduced,
      keyboard: true,
    });
    L.tileLayer(tileUrl, {
      attribution: safeAttributionHtml(attribution),
      maxZoom,
      // Die OSM Tile Usage Policy verlangt einen Referer ("no-referrer" ist daher keine Option).
      // "strict-origin" sendet nur die eigene Origin (ohne Pfad und Query) und ausschließlich über
      // HTTPS an den Kartenserver. Das steht in der Datenschutzerklärung (privacy.s7).
      referrerPolicy: "strict-origin",
    }).addTo(map);
    const layer = L.layerGroup().addTo(map);
    map.on("click", (e: L.LeafletMouseEvent) => {
      onPickRef.current?.({ lat: round5(e.latlng.lat), lon: round5(e.latlng.lng) });
    });
    mapRef.current = map;
    layerRef.current = layer;
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, [tileUrl, attribution, maxZoom]);

  // Marker und Routen zeichnen.
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();
    for (const route of routes) {
      L.polyline(route.points, {
        color: route.highlighted ? "#b3261e" : "#0b5cad",
        weight: route.highlighted ? 6 : 4,
        opacity: route.muted ? 0.35 : 0.9,
        interactive: false,
      }).addTo(layer);
    }
    for (const marker of markers) {
      L.marker([marker.position.lat, marker.position.lon], {
        icon: makeIcon(marker.kind),
        alt: marker.label,
        title: marker.label,
        keyboard: false,
      }).addTo(layer);
    }
  }, [markers, routes]);

  // Ausschnitt anpassen, wenn sich die Signatur ändert.
  const fitRef = useRef({ markers, routes });
  useEffect(() => {
    fitRef.current = { markers, routes };
  }, [markers, routes]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || fitSignature === undefined) return;
    const points: [number, number][] = [];
    for (const r of fitRef.current.routes) points.push(...r.points);
    for (const m of fitRef.current.markers) points.push([m.position.lat, m.position.lon]);
    if (points.length < 2) return;
    map.fitBounds(L.latLngBounds(points), {
      padding: [30, 30],
      maxZoom: 15,
      animate: !prefersReducedMotion(),
    });
  }, [fitSignature, tileUrl, attribution]);

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" role="region" aria-label={ariaLabel} aria-describedby={hintId} />
      {showCrosshair && <div className="map-crosshair" aria-hidden="true" />}
      <p id={hintId} className="sr-only">{t("map.keyboardHint")}</p>
      <p className="hint tiles-notice">{t("map.tilesNotice", { host: hostOf(tileUrl) || "[Kartenserver]" })}</p>
    </div>
  );
}
