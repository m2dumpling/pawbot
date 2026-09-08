export const LOCALE_STORAGE_KEY = "pawbot.locale";

export const supportedLocales = [
  { code: "en", label: "English", nativeLabel: "English" },
  { code: "zh-CN", label: "Chinese (Simplified)", nativeLabel: "简体中文" },
] as const;

export type SupportedLocale = (typeof supportedLocales)[number]["code"];

export const defaultLocale: SupportedLocale = "en";
export const fallbackLocale: SupportedLocale = "en";

export function normalizeLocale(
  input: string | null | undefined,
): SupportedLocale {
  if (!input) return defaultLocale;
  const trimmed = input.trim();
  if (!trimmed) return defaultLocale;

  const exact = supportedLocales.find((locale) => locale.code === trimmed);
  if (exact) return exact.code;

  const lower = trimmed.toLowerCase();
  if (lower === "zh" || lower.startsWith("zh-cn") || lower.startsWith("zh-sg")) {
    return "zh-CN";
  }

  const base = lower.split("-")[0];
  const baseMatch = supportedLocales.find(
    (locale) => locale.code.toLowerCase() === base,
  );
  return baseMatch?.code ?? defaultLocale;
}

function readStoredLocale(): SupportedLocale | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return raw ? normalizeLocale(raw) : null;
  } catch {
    return null;
  }
}

export function resolveInitialLocale(): SupportedLocale {
  return readStoredLocale() ?? defaultLocale;
}

export function persistLocale(locale: SupportedLocale): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // ignore storage errors
  }
}

export function applyDocumentLocale(locale: SupportedLocale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
}

export function localeOption(locale: SupportedLocale) {
  return supportedLocales.find((entry) => entry.code === locale) ?? supportedLocales[0];
}
