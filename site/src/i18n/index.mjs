// The site speaks the app's languages. English lives at the root (/app/), every other
// language under its code (/pl/app/); a page's path without that prefix is its "base".
import en from "./en.json";
import de from "./de.json";
import es from "./es.json";
import fr from "./fr.json";
import it from "./it.json";
import pl from "./pl.json";
import ru from "./ru.json";

// Code, endonym (as the app's Preferences → Language lists them) and Open Graph locale.
export const languages = [
  { code: "en", name: "English", locale: "en_US" },
  { code: "de", name: "Deutsch", locale: "de_DE" },
  { code: "es", name: "Español", locale: "es_ES" },
  { code: "fr", name: "Français", locale: "fr_FR" },
  { code: "it", name: "Italiano", locale: "it_IT" },
  { code: "pl", name: "Polski", locale: "pl_PL" },
  { code: "ru", name: "Русский", locale: "ru_RU" },
];

const strings = { en, de, es, fr, it, pl, ru };

export const langOf = (path) => {
  const first = path.split("/")[1];
  return first !== "en" && first in strings ? first : "en";
};
export const pathIn = (lang, base) => (lang === "en" ? base : `/${lang}${base}`);
export const baseOf = (path) => {
  const lang = langOf(path);
  return lang === "en" ? path : path.slice(lang.length + 1);
};
// The interface strings of the layout and components (not the pages' own text).
export const ui = (path) => strings[langOf(path)];
