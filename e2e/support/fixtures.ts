import { randomBytes } from "node:crypto";
import {
  test as base,
  expect,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { loginOk, login, registerUser, type User } from "./api";

// 1x1-PNG als Kachel: Der Kartenserver wird nie wirklich angesprochen (kein Internet nötig).
const TILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

export interface CapturedResponse {
  /** Pfad ohne Query (Koordinaten gehören nicht in Meldungen). */
  path: string;
  status: number;
  body: string;
}

/**
 * Netzwerkwächter je Browser-Kontext:
 *  - Anfragen an die eigene Origin laufen durch,
 *  - Anfragen an den konfigurierten Kachelserver beantwortet der Test selbst,
 *  - alles andere wird abgebrochen und als Verstoß vermerkt (der Test schlägt dann fehl),
 *  - alle Antworten unter /api/ werden mitgeschnitten (Prüfung auf Datenabfluss).
 */
export class NetworkGuard {
  readonly hosts = new Set<string>();
  readonly violations: string[] = [];
  tileRequests = 0;
  private readonly pending: Promise<CapturedResponse>[] = [];

  async install(context: BrowserContext, baseURL: string, tileHost: string): Promise<void> {
    const origin = new URL(baseURL).origin;
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      this.hosts.add(url.host);
      if (url.origin === origin) {
        await route.continue();
      } else if (url.host === tileHost) {
        this.tileRequests += 1;
        await route.fulfill({ status: 200, contentType: "image/png", body: TILE_PNG });
      } else {
        this.violations.push(`${url.protocol}//${url.host}`);
        await route.abort("blockedbyclient");
      }
    });
    context.on("response", (response) => {
      const url = new URL(response.url());
      if (url.origin !== origin || !url.pathname.startsWith("/api/")) return;
      this.pending.push(
        response.text().then(
          (body) => ({ path: url.pathname, status: response.status(), body }),
          () => ({ path: url.pathname, status: response.status(), body: "" }),
        ),
      );
    });
  }

  /** Alle bisher beendeten API-Antworten des Browsers (wartet auf noch laufende Bodies). */
  responses(): Promise<CapturedResponse[]> {
    return Promise.all([...this.pending]);
  }
}

export interface Actor {
  user: User;
  context: BrowserContext;
  page: Page;
  guard: NetworkGuard;
}

export function makeUser(label: string): User {
  const id = randomBytes(4).toString("hex");
  return {
    // klein geschrieben, weil die API E-Mail-Adressen normalisiert
    email: `e2e-${label}-${id}@example.org`,
    password: `pw-${randomBytes(9).toString("hex")}`,
    displayName: `E2E ${label[0]?.toUpperCase() ?? ""}${label.slice(1)} ${id}`,
  };
}

interface AppConfig {
  tileHost: string;
  tileUrl: string;
}

interface Fixtures {
  appConfig: AppConfig;
  guard: NetworkGuard;
  /** Konten, die der Test anlegt, werden am Ende (best effort) per API gelöscht. */
  trackUser: (user: User) => void;
  /** Eigener Browser-Kontext (Fahrer/Mitfahrer getrennt) mit Wächter, registriert und optional angemeldet. */
  newActor: (label: string, options?: { signIn?: boolean }) => Promise<Actor>;
}

export const test = base.extend<Fixtures>({
  appConfig: async ({ playwright, baseURL }, use) => {
    const api = await playwright.request.newContext({ baseURL: baseURL! });
    const res = await api.get("/api/config");
    expect(res.status(), "GET /api/config").toBe(200);
    const config = (await res.json()) as { map: { tile_url: string } };
    await api.dispose();
    const tileUrl = config.map.tile_url;
    await use({ tileUrl, tileHost: new URL(tileUrl.replace(/\{[a-z]\}/g, "0")).host });
  },

  trackUser: async ({ playwright, baseURL }, use) => {
    const users: User[] = [];
    await use((user) => {
      users.push(user);
    });
    for (const user of users) {
      const api = await playwright.request.newContext({ baseURL: baseURL! });
      try {
        const res = await login(api, user);
        if (res.status() === 200) await api.delete("/api/me");
      } finally {
        await api.dispose();
      }
    }
  },

  guard: [
    async ({ context, baseURL, appConfig }, use) => {
      const guard = new NetworkGuard();
      await guard.install(context, baseURL!, appConfig.tileHost);
      await use(guard);
      expect(guard.violations, "externe Anfragen (nur eigene Origin und Kachelserver erlaubt)").toEqual([]);
    },
    { auto: true },
  ],

  newActor: async (
    { browser, baseURL, locale, timezoneId, viewport, reducedMotion, appConfig, trackUser },
    use,
  ) => {
    const actors: Actor[] = [];
    const make = async (label: string, options: { signIn?: boolean } = {}): Promise<Actor> => {
      const user = makeUser(label);
      const context = await newContext(browser, {
        baseURL: baseURL!,
        locale: locale ?? "de-DE",
        timezoneId: timezoneId ?? "Europe/Berlin",
        viewport: viewport ?? { width: 1280, height: 900 },
        reducedMotion: reducedMotion ?? "reduce",
      });
      const guard = new NetworkGuard();
      await guard.install(context, baseURL!, appConfig.tileHost);
      const page = await context.newPage();
      trackUser(user);
      await registerUser(context.request, user);
      if (options.signIn) await loginOk(context.request, user);
      const actor = { user, context, page, guard };
      actors.push(actor);
      return actor;
    };
    await use(make);

    // Spuren und Bilder bei Fehlern erzeugt Playwright selbst (trace/screenshot in der Konfiguration)
    const violations: string[] = [];
    for (const actor of actors) {
      violations.push(...actor.guard.violations);
      await actor.context.close();
    }
    expect(violations, "externe Anfragen (nur eigene Origin und Kachelserver erlaubt)").toEqual([]);
  },
});

function newContext(
  browser: Browser,
  options: Parameters<Browser["newContext"]>[0],
): Promise<BrowserContext> {
  return browser.newContext(options);
}

export { expect };
