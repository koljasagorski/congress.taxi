import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { de, type MessageKey } from "./de";
import { en } from "./en";

export type Locale = "de" | "en";
export type { MessageKey };

type Params = Record<string, string | number>;

const dictionaries: Record<Locale, Partial<Record<MessageKey, string>>> = { de, en };

const PLURAL = /\{(\w+), plural, one \{([^}]*)\} other \{([^}]*)\}\}/g;

export function translate(locale: Locale, key: MessageKey, params: Params = {}): string {
  const template = dictionaries[locale][key] ?? de[key];
  const withPlural = template.replace(PLURAL, (_m, name: string, one: string, other: string) => {
    const n = Number(params[name]);
    return (n === 1 ? one : other).replace("#", String(n));
  });
  return withPlural.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export type TFunction = (key: MessageKey, params?: Params) => string;

interface I18nValue {
  locale: Locale;
  t: TFunction;
}

const I18nContext = createContext<I18nValue>({
  locale: "de",
  t: (key, params) => translate("de", key, params),
});

/** Sprache: Standard Deutsch. Englisch ist vorbereitet (siehe en.ts), aber nicht vollständig. */
export function I18nProvider({
  locale = "de",
  children,
}: {
  locale?: Locale;
  children: ReactNode;
}) {
  const t = useCallback<TFunction>((key, params) => translate(locale, key, params), [locale]);
  const value = useMemo(() => ({ locale, t }), [locale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
