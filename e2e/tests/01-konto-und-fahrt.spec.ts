// Flow (a): Registrieren (Einwilligung Pflicht) -> Anmelden -> Fahrt anbieten (Karte, Route) -> Abmelden
import { myRides, MAP_OFFSETS } from "../support/api";
import { test, expect, makeUser } from "../support/fixtures";
import { t } from "../support/i18n";
import { inHours, localInput } from "../support/time";
import { clickMap, main, uiLogin, waitForMap } from "../support/ui";

test("Registrieren, Anmelden, Fahrt anbieten und Abmelden", async ({ page, context, trackUser }) => {
  const user = makeUser("fahrer");
  trackUser(user);

  // --- Registrierung: ohne Einwilligung geht nichts raus ---
  await page.goto("/registrieren");
  const form = main(page);
  const registerCalls: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/api/auth/register")) registerCalls.push(req.url());
  });
  await form.getByLabel(t("field.email"), { exact: true }).fill(user.email);
  await form.getByLabel(t("field.password"), { exact: true }).fill(user.password);
  await form.getByLabel(t("field.displayName"), { exact: true }).fill(user.displayName);
  await form.getByRole("button", { name: t("register.submit") }).click();
  await expect(form.getByText(t("valid.consentRequired"))).toBeVisible();
  await expect(form.getByRole("checkbox")).toBeFocused();
  expect(registerCalls, "ohne Einwilligung darf keine Registrierung gesendet werden").toHaveLength(0);

  // Mit Einwilligung klappt es (202, kein Auto-Login)
  await form.getByRole("checkbox").check();
  await form.getByRole("button", { name: t("register.submit") }).click();
  await expect(form.getByRole("heading", { name: t("register.successTitle") })).toBeVisible();
  expect(registerCalls).toHaveLength(1);
  expect((await context.request.get("/api/me")).status(), "nach der Registrierung nicht angemeldet").toBe(401);

  // --- Anmelden: erst falsches, dann richtiges Passwort ---
  await form.getByRole("link", { name: t("register.toLogin") }).click();
  await form.getByLabel(t("field.email"), { exact: true }).fill(user.email);
  await form.getByLabel(t("field.password"), { exact: true }).fill(`${user.password}-falsch`);
  await form.getByRole("button", { name: t("login.submit"), exact: true }).click();
  await expect(form.getByRole("alert")).toHaveText(t("error.invalid_credentials"));
  await form.getByLabel(t("field.password"), { exact: true }).fill(user.password);
  await form.getByRole("button", { name: t("login.submit"), exact: true }).click();
  await expect(page.getByText(t("nav.signedInAs", { name: user.displayName }))).toBeVisible();
  const me = await context.request.get("/api/me");
  expect(me.status()).toBe(200);
  expect(((await me.json()) as { email: string }).email).toBe(user.email);

  // --- Fahrt anbieten: Karte anklicken, Abfahrt und Plätze, speichern ---
  await page
    .getByRole("navigation", { name: t("nav.label") })
    .getByRole("link", { name: t("nav.offer") })
    .click();
  await expect(main(page).getByRole("heading", { name: t("offer.title") })).toBeVisible();
  const point = await clickMap(page, MAP_OFFSETS.start);
  expect(point.lat).toBeGreaterThan(53.5);
  expect(point.lat).toBeLessThan(53.6);
  expect(point.lon).toBeGreaterThan(9.85);
  expect(point.lon).toBeLessThan(10.0);
  const departure = inHours(48);
  await main(page).getByLabel(t("field.departure")).fill(localInput(departure));
  await main(page).getByLabel(t("field.seats"), { exact: true }).fill("2");
  await main(page).getByRole("button", { name: t("offer.submit") }).click();

  // Erfolgsseite: Strecke und Fahrzeit vom Server, Routenlinie auf der Karte
  await expect(main(page).getByRole("heading", { name: t("offer.successTitle") })).toBeVisible();
  await expect(main(page).locator(".notice-success")).toContainText(/Route: \d+(,\d+)? km, etwa \d+ Minuten/);
  await waitForMap(page);
  const routePath = main(page).locator(".leaflet-overlay-pane svg path");
  await expect(routePath).toHaveCount(1);
  expect(((await routePath.getAttribute("d")) ?? "").length, "Routenlinie hat viele Stützpunkte").toBeGreaterThan(60);

  // Gespeichert: in "Meine Fahrten" und per API, mit Route
  await main(page).getByRole("link", { name: t("offer.toMine") }).click();
  await expect(main(page).getByText(t("mine.seatsOf", { available: 2, total: 2 }))).toBeVisible();
  const rides = await myRides(context.request);
  expect(rides).toHaveLength(1);
  expect(rides[0]?.status).toBe("open");
  expect(rides[0]?.seats_total).toBe(2);
  expect(rides[0]?.route.length, "gespeicherte Route").toBeGreaterThan(2);

  // --- Abmelden ---
  await page.getByRole("button", { name: t("nav.logout") }).click();
  await expect(page.getByRole("link", { name: t("nav.login") }).first()).toBeVisible();
  expect((await context.request.get("/api/me")).status(), "nach dem Abmelden").toBe(401);
  await page.goto("/meine-fahrten");
  await expect(page).toHaveURL(/\/anmelden\?next=%2Fmeine-fahrten$/);

  // Wieder anmelden über den Rücksprung funktioniert (Sitzung ist wirklich beendet, kein Cookie-Rest)
  await uiLogin(page, user);
});

test("Registrierung: Pflichtfelder und Passwortlänge werden im Formular geprüft", async ({ page }) => {
  await page.goto("/registrieren");
  const form = main(page);
  await form.getByLabel(t("field.email"), { exact: true }).fill("kein-mail");
  await form.getByLabel(t("field.password"), { exact: true }).fill("kurz");
  await form.getByRole("button", { name: t("register.submit") }).click();
  await expect(form.getByText(t("valid.emailInvalid"))).toBeVisible();
  await expect(form.getByText(t("valid.passwordLength"))).toBeVisible();
  await expect(form.getByText(t("valid.displayNameRequired"))).toBeVisible();
  await expect(form.getByText(t("valid.consentRequired"))).toBeVisible();
  // Fokus springt auf das erste fehlerhafte Feld
  await expect(form.getByLabel(t("field.email"), { exact: true })).toBeFocused();
});
