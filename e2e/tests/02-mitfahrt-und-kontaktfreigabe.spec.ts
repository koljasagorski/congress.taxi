// Flow (b): Fahrer und Mitfahrer in zwei Browser-Kontexten. Suche per Klick, Treffer mit Umweg,
// Anfrage, Annahme; die E-Mail der Gegenseite erscheint erst nach der Freigabe - auch nicht in
// Netzwerkantworten.
import { MAP_OFFSETS, myRides } from "../support/api";
import { test, expect, type CapturedResponse } from "../support/fixtures";
import { t } from "../support/i18n";
import { inHours, localInput, plusMinutes } from "../support/time";
import { clickMap, main } from "../support/ui";

function expectNoEmail(responses: CapturedResponse[], email: string, what: string): void {
  for (const r of responses) {
    expect(r.body.toLowerCase(), `${what}: ${r.path}`).not.toContain(email.toLowerCase());
  }
}

test("Fahrer legt Fahrt an, Mitfahrer findet sie, fragt an, Fahrer nimmt an, Kontakt erst danach", async ({
  newActor,
}) => {
  const driver = await newActor("fahrer", { signIn: true });
  const rider = await newActor("mitfahrer", { signIn: true });
  const departure = inHours(72, Math.floor(Math.random() * 600));

  // --- Fahrer: Fahrt anbieten (Klick auf die Karte) ---
  await driver.page.goto("/fahrt-anbieten");
  await clickMap(driver.page, MAP_OFFSETS.start);
  await main(driver.page).getByLabel(t("field.departure")).fill(localInput(departure));
  await main(driver.page).getByLabel(t("field.seats"), { exact: true }).fill("2");
  await main(driver.page).getByRole("button", { name: t("offer.submit") }).click();
  await expect(main(driver.page).getByRole("heading", { name: t("offer.successTitle") })).toBeVisible();

  // --- Mitfahrer: Abholpunkt anklicken, engen Zeitraum wählen, suchen ---
  await rider.page.goto("/mitfahrt-suchen");
  const pickup = await clickMap(rider.page, MAP_OFFSETS.pickup);
  expect(pickup.lat).toBeGreaterThan(53.5);
  await main(rider.page).getByLabel(t("field.timeFrom")).fill(localInput(plusMinutes(departure, -30)));
  await main(rider.page).getByLabel(t("field.timeTo")).fill(localInput(plusMinutes(departure, 30)));
  await main(rider.page).getByRole("button", { name: t("search.submit") }).click();

  // Treffer wird über den eindeutigen Anzeigenamen gefunden (nicht über Reihenfolge/Anzahl)
  const card = main(rider.page)
    .getByRole("listitem")
    .filter({ has: rider.page.getByRole("heading", { name: t("search.driver", { name: driver.user.displayName }) }) });
  await expect(card).toBeVisible();
  await expect(card.locator(".detour")).toHaveText(/Umweg: ≈ \+\d+(,\d)? km, ≈ \+\d+ Min\. \(≈ \+\d+ %\)/);
  await card.getByRole("button", { name: t("search.request") }).click();
  await expect(card.getByText(/Anfrage gesendet\./)).toBeVisible();

  // --- Vor der Annahme: nirgends die E-Mail der Gegenseite ---
  await driver.page.goto("/meine-fahrten");
  const requestRow = main(driver.page)
    .locator("li.request")
    .filter({ hasText: t("mine.requestFrom", { name: rider.user.displayName }) });
  await expect(requestRow).toBeVisible();
  await expect(requestRow.getByText(t("mine.contactHidden"))).toBeVisible();
  await expect(main(driver.page)).not.toContainText(rider.user.email);
  await rider.page.goto("/meine-fahrten");
  const outgoingRow = main(rider.page).locator("li.request").filter({ hasText: `Fahrt von ${driver.user.displayName}` });
  await expect(outgoingRow.getByText(t("mine.contactHidden"))).toBeVisible();
  await expect(main(rider.page)).not.toContainText(driver.user.email);

  const riderBefore = await rider.guard.responses();
  const driverBefore = await driver.guard.responses();
  // Positivkontrolle: die Antworten wurden wirklich mitgeschnitten (Treffer, Anfragen)
  expect(riderBefore.some((r) => r.path === "/api/match" && r.body.includes(driver.user.displayName))).toBe(true);
  expect(riderBefore.some((r) => r.path === "/api/requests/outgoing")).toBe(true);
  expect(driverBefore.some((r) => r.path === "/api/requests/incoming" && r.body.includes(rider.user.displayName))).toBe(true);
  expectNoEmail(riderBefore, driver.user.email, "Mitfahrer vor Annahme");
  expectNoEmail(driverBefore, rider.user.email, "Fahrer vor Annahme");
  // Die Treffer-Antwort kennt nie eine E-Mail-Adresse oder ein E-Mail-Feld
  for (const r of riderBefore.filter((x) => x.path === "/api/match")) expect(r.body).not.toMatch(/mail/i);

  // --- Fahrer nimmt an: Platz wird gezählt, E-Mail des Mitfahrers erscheint ---
  await requestRow.getByRole("button", { name: t("mine.accept") }).click();
  await expect(main(driver.page).getByText(t("mine.done.accepted"))).toBeVisible();
  await expect(requestRow.getByRole("link", { name: rider.user.email })).toBeVisible();
  await expect(main(driver.page).getByText(t("mine.seatsOf", { available: 1, total: 2 }))).toBeVisible();

  // Mitfahrer sieht nach Neuladen die E-Mail des Fahrers
  await rider.page.reload();
  await expect(outgoingRow.getByRole("link", { name: driver.user.email })).toBeVisible();

  // Nach der Freigabe stehen die Adressen genau dort, wo sie hingehören (Positivkontrolle)
  const riderAfter = await rider.guard.responses();
  const driverAfter = await driver.guard.responses();
  expect(riderAfter.some((r) => r.path === "/api/requests/outgoing" && r.body.includes(driver.user.email))).toBe(true);
  expect(driverAfter.some((r) => r.body.includes(rider.user.email))).toBe(true);
  // ... aber niemals im Treffer-Endpunkt
  for (const r of [...riderAfter, ...driverAfter].filter((x) => x.path === "/api/match")) {
    expect(r.body).not.toMatch(/mail/i);
  }

  // Serverseitig: Fahrt hat einen Platz weniger, eine angenommene Anfrage
  const [ride] = await myRides(driver.context.request);
  expect(ride?.seats_available).toBe(1);
  expect(ride?.accepted_requests).toBe(1);
});
