import { expect, type Page } from "@playwright/test";
import type { LatLon, User } from "./api";
import { t } from "./i18n";

export const main = (page: Page) => page.getByRole("main");

/** Warte, bis die Karte steht (Leaflet hat mindestens eine Kachel geladen). */
export async function waitForMap(page: Page): Promise<void> {
  await expect(page.locator("main .map")).toBeVisible();
  await expect(page.locator("main .leaflet-tile-loaded").first()).toBeAttached();
}

/**
 * Klick auf die Karte, `dx`/`dy` Pixel von der Mitte (= Veranstaltungsort, dort sitzt der Marker).
 * Gibt den übernommenen Punkt zurück (aus den Koordinatenfeldern gelesen).
 */
export async function clickMap(page: Page, offset: { dx: number; dy: number }): Promise<LatLon> {
  await waitForMap(page);
  const map = page.locator("main .map").first();
  await map.scrollIntoViewIfNeeded();
  const box = await map.boundingBox();
  if (!box) throw new Error("Karte nicht sichtbar");
  await map.click({ position: { x: box.width / 2 + offset.dx, y: box.height / 2 + offset.dy } });
  const latField = page.getByLabel(t("field.lat"));
  const lonField = page.getByLabel(t("field.lon"));
  await expect(latField).not.toHaveValue("");
  await expect(page.getByText(/^Punkt gesetzt:/)).toBeVisible();
  return { lat: Number(await latField.inputValue()), lon: Number(await lonField.inputValue()) };
}

export async function uiRegister(page: Page, user: User): Promise<void> {
  await page.goto("/registrieren");
  const form = main(page);
  await form.getByLabel(t("field.email"), { exact: true }).fill(user.email);
  await form.getByLabel(t("field.password"), { exact: true }).fill(user.password);
  await form.getByLabel(t("field.displayName"), { exact: true }).fill(user.displayName);
  await form.getByRole("checkbox").check();
  await form.getByRole("button", { name: t("register.submit") }).click();
  await expect(form.getByRole("heading", { name: t("register.successTitle") })).toBeVisible();
}

export async function uiLogin(page: Page, user: User): Promise<void> {
  await page.goto("/anmelden");
  const form = main(page);
  await form.getByLabel(t("field.email"), { exact: true }).fill(user.email);
  await form.getByLabel(t("field.password"), { exact: true }).fill(user.password);
  await form.getByRole("button", { name: t("login.submit"), exact: true }).click();
  await expect(page.getByText(t("nav.signedInAs", { name: user.displayName }))).toBeVisible();
}
