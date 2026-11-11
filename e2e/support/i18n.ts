// Texte kommen aus dem Frontend selbst: Ändert sich ein Text dort, bleiben die Tests synchron.
import { de } from "../../web/src/i18n/de";

export type Key = keyof typeof de;
type Params = Record<string, string | number>;

const PLURAL = /\{(\w+), plural, one \{([^}]*)\} other \{([^}]*)\}\}/g;

export function t(key: Key, params: Params = {}): string {
  const template: string = de[key];
  const withPlural = template.replace(PLURAL, (_m, name: string, one: string, other: string) => {
    const n = Number(params[name]);
    return (n === 1 ? one : other).replace("#", String(n));
  });
  return withPlural.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}
