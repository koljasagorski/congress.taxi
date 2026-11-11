// Flow (e): Reflow bei 360 px (WCAG 1.4.10). Sehr lange, unzerbrechliche Anzeigenamen und E-Mail-Adressen
// (laut API zulässig) dürfen die Seite nicht horizontal scrollbar machen - auch nicht in der Kontaktfreigabe.
import { randomBytes } from "node:crypto";
import { MAP_OFFSETS, POINTS, acceptRequest, createRequest, createRide, myRides, type User } from "../support/api";
import { test, expect } from "../support/fixtures";
import { t } from "../support/i18n";
import { inHours, localInput, plusMinutes } from "../support/time";
import { clickMap, main } from "../support/ui";
import type { Page } from "@playwright/test";

test.use({ viewport: { width: 360, height: 740 } });

/** Anzeigename mit genau 80 Zeichen ohne Leerzeichen (Obergrenze der API). */
function longName(prefix: string): string {
  return `${prefix}${randomBytes(4).toString("hex")}`.padEnd(80, "W");
}

/** Realistisch lange Adresse (ca. 100 Zeichen, Local-Part 60 Zeichen, nur Kleinbuchstaben). */
function longEmail(prefix: string): string {
  const id = randomBytes(4).toString("hex");
  return `${prefix}-${id}`.padEnd(60, "x") + "@sehr-lange-subdomain.universitaet-musterstadt.example.org";
}

/** Misst Überlauf: Seitenbreite und rechter Rand des breitesten Elements gegen die Fensterbreite. */
async function expectNoHorizontalScroll(page: Page, what: string): Promise<void> {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    let maxRight = 0;
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      if (el.closest(".leaflet-container")) continue; // Karten-Kacheln ragen konstruktionsbedingt heraus
      maxRight = Math.max(maxRight, el.getBoundingClientRect().right);
    }
    return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, maxRight: Math.ceil(maxRight) };
  });
  expect(m.clientWidth, `${what}: Fensterbreite`).toBe(360);
  expect(m.scrollWidth, `${what}: scrollWidth`).toBeLessThanOrEqual(m.clientWidth);
  expect(m.maxRight, `${what}: rechter Rand des breitesten Elements`).toBeLessThanOrEqual(m.clientWidth);
}

function longUser(label: string): Partial<User> {
  return { email: longEmail(label), displayName: longName(`${label[0]?.toUpperCase() ?? ""}${label.slice(1)}`) };
}

test("lange Namen und E-Mail-Adressen sprengen bei 360 px das Layout nicht", async ({ newActor }) => {
  const driver = await newActor("fahrer", { signIn: true, user: longUser("fahrer") });
  const rider = await newActor("mitfahrer", { signIn: true, user: longUser("mitfahrer") });
  expect(driver.user.displayName).toHaveLength(80);
  expect(driver.user.email.length).toBeGreaterThan(100);
  const departure = inHours(72, Math.floor(Math.random() * 600));

  // Kopfzeile ("Angemeldet als ...") und Startseite
  await driver.page.goto("/");
  await expect(driver.page.getByText(t("nav.signedInAs", { name: driver.user.displayName }))).toBeVisible();
  await expectNoHorizontalScroll(driver.page, "Startseite (Kopfzeile mit langem Namen)");

  // Fahrt per API, Treffer in der Oberfläche
  const ride = await createRide(driver.context.request, POINTS.start, departure, 2);
  await rider.page.goto("/mitfahrt-suchen");
  await clickMap(rider.page, MAP_OFFSETS.pickup);
  await main(rider.page).getByLabel(t("field.timeFrom")).fill(localInput(plusMinutes(departure, -30)));
  await main(rider.page).getByLabel(t("field.timeTo")).fill(localInput(plusMinutes(departure, 30)));
  await main(rider.page).getByRole("button", { name: t("search.submit") }).click();
  const card = main(rider.page)
    .getByRole("listitem")
    .filter({ has: rider.page.getByRole("heading", { name: t("search.driver", { name: driver.user.displayName }) }) });
  await expect(card).toBeVisible();
  await expectNoHorizontalScroll(rider.page, "Trefferliste mit langem Fahrernamen");

  // Anfrage und Annahme per API, dann Kontaktfreigabe in Meine Fahrten
  const request = await createRequest(rider.context.request, ride.id, POINTS.pickup);
  await acceptRequest(driver.context.request, request.id);
  expect((await myRides(driver.context.request))[0]?.accepted_requests).toBe(1);

  await driver.page.goto("/meine-fahrten");
  const incoming = main(driver.page).locator("li.request").filter({ hasText: rider.user.displayName });
  await expect(incoming.getByRole("link", { name: rider.user.email })).toBeVisible();
  await expectNoHorizontalScroll(driver.page, "Meine Fahrten (Fahrer): Kontakt des Mitfahrers");

  await rider.page.goto("/meine-fahrten");
  const outgoing = main(rider.page).locator("li.request").filter({ hasText: driver.user.displayName });
  await expect(outgoing.getByRole("link", { name: driver.user.email })).toBeVisible();
  await expectNoHorizontalScroll(rider.page, "Meine Fahrten (Mitfahrer): Kontakt des Fahrers");
});
