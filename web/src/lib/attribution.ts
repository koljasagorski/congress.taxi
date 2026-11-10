// Quellenangabe der Karte aus GET /api/config (map.attribution).
//
// Der Wert kommt vom Betreiber (Umgebungsvariable TILE_ATTRIBUTION) und ist einfacher Text mit
// optionalen Links <a href="https://…">…</a>. Leaflet schreibt die Angabe als HTML in die Karte; damit
// daraus nie ein Einfallstor wird, wird hier alles außer Text und https-Links in reinen Text umgewandelt.

/** Rückfall, falls der Server keine Angabe liefert (Vertrag verlangt sie): Text ohne Link. */
export const FALLBACK_ATTRIBUTION = "© OpenStreetMap-Mitwirkende";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHttpsUrl(href: string | null): string | null {
  if (!href) return null;
  try {
    const url = new URL(href);
    return url.protocol === "https:" && url.username === "" && url.password === "" ? url.href : null;
  } catch {
    return null;
  }
}

/**
 * Liefert sicheres HTML für Leaflets Attribution. Erlaubt sind Text (HTML-Entitäten wie &copy; werden
 * aufgelöst) und `<a href="https://…">`-Links (werden mit rel="noopener noreferrer" in neuem Tab geöffnet).
 * Alles andere (Skripte, Bilder, Attribute, andere Schemata) erscheint nur als Text bzw. entfällt.
 */
export function safeAttributionHtml(raw: string | null | undefined): string {
  const source = raw?.trim() ? raw : FALLBACK_ATTRIBUTION;
  const doc = new DOMParser().parseFromString(`<body>${source}</body>`, "text/html");
  let out = "";
  const walk = (node: Node): void => {
    node.childNodes.forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) {
        out += escapeHtml(child.textContent ?? "");
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const el = child as Element;
        const tag = el.tagName.toLowerCase();
        if (tag === "a") {
          const href = safeHttpsUrl(el.getAttribute("href"));
          if (href) {
            out += `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">`;
            walk(el);
            out += "</a>";
            return;
          }
          walk(el);
        } else if (tag !== "script" && tag !== "style") {
          walk(el); // unbekanntes Element: nur der Textinhalt bleibt
        }
      }
    });
  };
  walk(doc.body);
  return out.trim() ? out : escapeHtml(FALLBACK_ATTRIBUTION);
}
