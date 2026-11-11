const TZ = "Europe/Berlin"; // wie timezoneId in playwright.config.ts

/** Zeitpunkt in `hours` Stunden (auf volle Minuten gerundet, ohne Sekunden). */
export function inHours(hours: number, extraMinutes = 0): Date {
  const ms = Date.now() + hours * 3_600_000 + extraMinutes * 60_000;
  return new Date(Math.floor(ms / 60_000) * 60_000);
}

/** Wert für <input type="datetime-local"> in der Zeitzone des Browsers: YYYY-MM-DDTHH:mm. */
export function localInput(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string): string => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function plusMinutes(date: Date, minutes: number): Date {
  return new Date(date.getTime() + minutes * 60_000);
}
