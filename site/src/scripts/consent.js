// The Google tag is requested only after a valid, persisted opt-in.
const panel = document.getElementById("consent");
if (panel && !document.documentElement.dataset.redirecting) setup(panel);

function setup(panel) {
  const key = "djdAnalyticsConsent";
  const revision = 1;
  const id = panel.dataset.ga;
  const settings = document.querySelector("[data-settings]");
  const disable = `ga-disable-${id}`;
  let started = false;
  let timer;

  function decision() {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      if (value?.revision === revision && Number.isFinite(value.until) && value.until > Date.now()
          && ["allow", "deny"].includes(value.choice)) return value;
    } catch { /* Storage blocked or cleared. */ }
    return null;
  }

  function gtag() { window.dataLayer.push(arguments); }

  function start() {
    started = true;
    window[disable] = false;
    window.dataLayer ??= [];
    gtag("consent", "default", {
      analytics_storage: "granted", ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    });
    gtag("js", new Date());
    const referrer = document.referrer ? new URL(document.referrer).origin : "";
    gtag("config", id, {
      allow_google_signals: false, allow_ad_personalization_signals: false,
      cookie_domain: "none", cookie_expires: 180 * 86400, cookie_update: false,
      page_location: location.origin + panel.dataset.page, page_referrer: referrer,
    });
    // The config command records this page once when the tag loads.
    const tag = document.createElement("script");
    tag.async = true;
    tag.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    document.head.append(tag);
  }

  function clearCookies() {
    for (const name of ["_ga", "_ga_" + id.slice(2)]) {
      document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
    }
  }

  function sync() {
    clearTimeout(timer);
    const saved = decision();
    panel.hidden = !!saved;
    if (saved) timer = setTimeout(sync, Math.min(saved.until - Date.now() + 1, 2147483647));
    if (saved?.choice === "allow") {
      if (!started) {
        try {
          // A readable but write-protected store cannot reliably remember a withdrawal.
          localStorage.removeItem(key + ".probe");
        } catch {
          window[disable] = true;
          panel.hidden = false;
          return;
        }
        start();
      }
    } else if (started) {
      window[disable] = true;
      clearCookies();
      location.reload(); // Unload the third-party script and its listeners.
    } else {
      window[disable] = true;
    }
  }

  function measure(event, parameters) {
    if (started && decision()?.choice === "allow") {
      gtag("event", event, { ...parameters, release_version: panel.dataset.version, language: document.documentElement.lang });
    }
  }

  settings.hidden = false;
  settings.addEventListener("click", () => {
    panel.hidden = false;
    panel.querySelector("h2").focus();
  });
  for (const button of panel.querySelectorAll("[data-choice]")) {
    button.addEventListener("click", () => {
      const until = new Date();
      const day = until.getDate();
      until.setMonth(until.getMonth() + 7, 0);
      until.setDate(Math.min(day, until.getDate()));
      const choice = button.dataset.choice;
      try {
        localStorage.setItem(key, JSON.stringify({ revision, choice, until: +until }));
      } catch {
        // Clear an earlier opt-in even if storage is full or has become read-only.
        if (choice === "deny") {
          try { localStorage.removeItem(key); } catch { /* Storage access is blocked. */ }
        }
      }
      if (choice === "deny") {
        clearCookies();
        if (started && decision()?.choice === "allow") {
          window[disable] = true;
          location.reload();
          return;
        }
      }
      sync();
      panel.hidden = true;
      document.getElementById("content").focus({ preventScroll: true });
    });
  }
  document.addEventListener("click", (event) => {
    const target = event.target.closest?.("[data-installer]")?.dataset.installer;
    if (["windows-x64", "macos-arm64", "macos-x86_64"].includes(target)) {
      const [platform, architecture] = target.split("-");
      measure("download_" + target.replace("-", "_"), { platform_name: platform, architecture });
    }
  });
  document.addEventListener("djd:linux-command-copied", () => measure("copy_linux_command", { platform_name: "linux" }));
  addEventListener("storage", (event) => {
    if (event.key === key || event.key === null) sync();
  });
  addEventListener("pageshow", sync);
  sync();
}
