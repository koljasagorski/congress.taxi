// Flow (c): Konto löschen. Danach ist kein Login mehr möglich, die Daten sind weg (per API geprüft)
// und die Sitze des Fahrers sind wieder frei.
import { acceptRequest, createRequest, createRide, login, match, myRides, POINTS } from "../support/api";
import { test, expect } from "../support/fixtures";
import { t } from "../support/i18n";
import { inHours, plusMinutes } from "../support/time";
import { main } from "../support/ui";

test("Mitfahrer und Fahrer löschen ihr Konto: Login unmöglich, Daten weg, Sitze wieder frei", async ({
  newActor,
  playwright,
  baseURL,
}) => {
  const driver = await newActor("fahrer", { signIn: true });
  const rider = await newActor("mitfahrer", { signIn: true });
  const observer = await newActor("beobachter", { signIn: true });
  const departure = inHours(96, Math.floor(Math.random() * 600));
  const from = plusMinutes(departure, -30);
  const to = plusMinutes(departure, 30);

  // --- Ausgangslage per API: Fahrt mit 2 Plätzen, Anfrage, Annahme -> 1 Platz frei ---
  const ride = await createRide(driver.context.request, POINTS.start, departure, 2);
  const request = await createRequest(rider.context.request, ride.id, POINTS.pickup);
  await acceptRequest(driver.context.request, request.id);
  const [before] = await myRides(driver.context.request);
  expect(before?.seats_available).toBe(1);
  expect(before?.accepted_requests).toBe(1);
  // Positivkontrolle für später: Dritte finden die Fahrt
  const found = await match(observer.context.request, POINTS.pickup, from, to);
  expect(found.items.map((m) => m.driver_display_name)).toContain(driver.user.displayName);

  // --- Mitfahrer löscht das Konto über die Oberfläche ---
  await rider.page.goto("/konto-loeschen");
  const form = main(rider.page);
  await form.getByRole("button", { name: t("delete.submit") }).click(); // ohne Haken: Hinweis, nichts passiert
  await expect(form.getByText(t("delete.confirmRequired"))).toBeVisible();
  expect((await rider.context.request.get("/api/me")).status(), "ohne Bestätigung nicht gelöscht").toBe(200);
  await form.getByLabel(t("delete.confirm")).check();
  await form.getByRole("button", { name: t("delete.submit") }).click();
  await expect(form.getByRole("heading", { name: t("delete.doneTitle") })).toBeVisible();

  // Login unmöglich (Antwort wie bei falschem Passwort), Sitzung beendet
  const probe = await playwright.request.newContext({ baseURL: baseURL! });
  expect((await login(probe, rider.user)).status()).toBe(401);
  expect((await rider.context.request.get("/api/me")).status()).toBe(401);
  expect((await rider.context.request.get("/api/requests/outgoing")).status()).toBe(401);

  // Sitz des Fahrers ist wieder frei, Anfrage ist weg - in der API ...
  const [afterRider] = await myRides(driver.context.request);
  expect(afterRider?.seats_available).toBe(2);
  expect(afterRider?.status).toBe("open");
  expect(afterRider?.accepted_requests).toBe(0);
  expect(afterRider?.pending_requests).toBe(0);
  const incoming = await driver.context.request.get("/api/requests/incoming");
  expect(((await incoming.json()) as { items: unknown[]; total: number }).total).toBe(0);
  // ... und in der Oberfläche
  await driver.page.goto("/meine-fahrten");
  await expect(main(driver.page).getByText(t("mine.seatsOf", { available: 2, total: 2 }))).toBeVisible();
  await expect(main(driver.page).getByText(t("mine.incomingEmpty"))).toBeVisible();
  await expect(main(driver.page)).not.toContainText(rider.user.displayName);
  await expect(main(driver.page)).not.toContainText(rider.user.email);

  // --- Fahrer löscht das Konto ---
  await driver.page.goto("/konto-loeschen");
  await main(driver.page).getByLabel(t("delete.confirm")).check();
  await main(driver.page).getByRole("button", { name: t("delete.submit") }).click();
  await expect(main(driver.page).getByRole("heading", { name: t("delete.doneTitle") })).toBeVisible();

  // Login in der Oberfläche scheitert, per API ebenfalls
  await driver.page.goto("/anmelden");
  await main(driver.page).getByLabel(t("field.email"), { exact: true }).fill(driver.user.email);
  await main(driver.page).getByLabel(t("field.password"), { exact: true }).fill(driver.user.password);
  await main(driver.page).getByRole("button", { name: t("login.submit"), exact: true }).click();
  await expect(main(driver.page).getByRole("alert")).toHaveText(t("error.invalid_credentials"));
  expect((await login(probe, driver.user)).status()).toBe(401);
  expect((await driver.context.request.get("/api/rides/mine")).status()).toBe(401);

  // Die Fahrt ist für Dritte verschwunden (vorher gefunden, siehe oben)
  const gone = await match(observer.context.request, POINTS.pickup, from, to);
  expect(gone.items.map((m) => m.driver_display_name)).not.toContain(driver.user.displayName);
  expect(gone.items.map((m) => m.ride_id)).not.toContain(ride.id);
  await probe.dispose();
});
