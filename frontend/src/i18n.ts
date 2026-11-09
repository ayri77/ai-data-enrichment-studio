import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import uk from "./locales/uk.json";
import de from "./locales/de.json";
import { initialLocale, locales, savePreference } from "./preferences";

export const messages = { en, uk, de };
void i18n.use(initReactI18next).init({
    resources: {
        en: { translation: en },
        uk: { translation: uk },
        de: { translation: de },
    },
    lng: initialLocale(),
    supportedLngs: [...locales],
    fallbackLng: "en",
    initAsync: false,
    keySeparator: false,
    interpolation: { escapeValue: false },
});
i18n.on("languageChanged", (language) => {
    if (locales.includes(language as (typeof locales)[number])) {
        savePreference("language", language);
        document.documentElement.lang = language;
    }
});
export default i18n;
