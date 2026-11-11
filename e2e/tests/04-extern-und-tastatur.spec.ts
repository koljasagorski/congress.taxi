// Kein externer Request außer dem konfigurierten Kachelserver; Tastaturbedienung der Hauptformulare.
import { POINTS } from "../support/api";
import { test, expect, makeUser } from "../support/fixtures";
import { t } from "../support/i18n";
import { inHours, localInput } from "../support/time";
import { main, uiLogin, uiRegister, waitForMap } from "../support/ui";

test("Keine externen Requests außer dem Kachelserver (Kacheln werden abgefangen)", async ({
  page,
  guard,
  appConfig,
  baseURL,
  trackUser,
}) => {
  const user = makeUser("pruefer");
  trackUser(user);
  const base = new URL(baseURL!);

  const landing = await page.goto("/");
  const csp = (await landing?.headerValue("content-security-policy")) ?? "";
  expect(csp, "CSP nennt den Kachelserver aus /api/config").toContain(`https://${appConfig.tileHost}`);
  for (const path of ["/datenschutz", "/impressum", "/mitfahrt-suchen"]) {
    await page.goto(path);
    await expect(main(page)).toBeVisible();
  }
  await uiRegister(page, user);
  await uiLogin(page, user);
  await page.goto("/fahrt-anbieten");
  await waitForMap(page);
  await page.goto("/mitfahrt-suchen");
  await waitForMap(page);

  // Positivkontrolle: Die Karte hat wirklich Kacheln angefragt (und der Test hat sie beantwortet)
  expect(guard.tileRequests).toBeGreaterThan(0);
  expect([...guard.hosts].sort()).toEqual([appConfig.tileHost, base.host].sort());
  expect(guard.violations).toEqual([]);

  // Auch im DOM stehen keine fremden Ressourcen
  const external = await page.evaluate((own) => {
    const urls = [
      ...document.querySelectorAll<HTMLElement>("script[src],link[href],img[src],iframe[src],source[src]"),
    ].map((el) => el.getAttribute("src") ?? el.getAttribute("href") ?? "");
    return urls.filter((u) => /^(https?:)?\/\//.test(u) && new URL(u, location.href).host !== own);
  }, base.host);
  const tileOnly = external.filter((u) => new URL(u, base.origin).host !== appConfig.tileHost);
  expect(tileOnly).toEqual([]);
});

test("Tastaturbedienung: Registrieren, Anmelden und Fahrt anbieten ohne Maus", async ({
  page,
  context,
  trackUser,
}) => {
  const user = makeUser("tastatur");
  trackUser(user);
  const form = main(page);
  const field = (key: "field.email" | "field.password" | "field.displayName") =>
    form.getByLabel(t(key), { exact: true });

  // --- Registrieren: Tab-Reihenfolge E-Mail, Passwort, Name, Einwilligung (Leertaste), Link, Absenden (Enter) ---
  await page.goto("/registrieren");
  await field("field.email").focus();
  await page.keyboard.type(user.email);
  await page.keyboard.press("Tab");
  await expect(field("field.password")).toBeFocused();
  await page.keyboard.type(user.password);
  await page.keyboard.press("Tab");
  await expect(field("field.displayName")).toBeFocused();
  await page.keyboard.type(user.displayName);
  await page.keyboard.press("Tab");
  await expect(form.getByRole("checkbox")).toBeFocused();
  await page.keyboard.press("Space");
  await expect(form.getByRole("checkbox")).toBeChecked();
  await page.keyboard.press("Tab");
  await expect(form.getByRole("link", { name: "Datenschutzhinweise" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(form.getByRole("button", { name: t("register.submit") })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(form.getByRole("heading", { name: t("register.successTitle") })).toBeVisible();

  // --- Anmelden: Passwortfeld + Enter sendet das Formular ---
  await page.goto("/anmelden");
  await field("field.email").focus();
  await page.keyboard.type(user.email);
  await page.keyboard.press("Tab");
  await expect(field("field.password")).toBeFocused();
  await page.keyboard.type(user.password);
  await page.keyboard.press("Enter");
  await expect(page.getByText(t("nav.signedInAs", { name: user.displayName }))).toBeVisible();

  // --- Fahrt anbieten: Punkt per Koordinaten statt Mausklick (Tastatur-Alternative zur Karte) ---
  await page.goto("/fahrt-anbieten");
  await waitForMap(page);
  const lat = form.getByLabel(t("field.lat"));
  const lon = form.getByLabel(t("field.lon"));
  await lat.focus();
  await page.keyboard.type(String(POINTS.start.lat));
  await page.keyboard.press("Tab");
  await expect(lon).toBeFocused();
  await page.keyboard.type(String(POINTS.start.lon));
  await page.keyboard.press("Enter"); // übernimmt die Koordinaten, sendet das Formular NICHT
  await expect(form.getByText(/^Punkt gesetzt:/)).toBeVisible();
  await expect(form.getByRole("heading", { name: t("offer.title") })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(form.getByRole("button", { name: t("map.applyCoords") })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(form.getByRole("button", { name: t("map.useCenter") })).toBeFocused();
  await page.keyboard.press("Tab");
  const departure = form.getByLabel(t("field.departure"));
  await expect(departure).toBeFocused();
  // Datum/Uhrzeit bestehen aus mehreren Tab-Stopps (Segmente) und werden daher per fill() gesetzt
  await departure.fill(localInput(inHours(120)));
  const seats = form.getByLabel(t("field.seats"), { exact: true });
  await seats.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("3");
  await page.keyboard.press("Tab");
  await expect(form.getByRole("button", { name: t("offer.submit") })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(form.getByRole("heading", { name: t("offer.successTitle") })).toBeVisible();
  const rides = await (await context.request.get("/api/rides/mine")).json();
  expect(rides.items).toHaveLength(1);
  expect(rides.items[0].seats_total).toBe(3);
});
