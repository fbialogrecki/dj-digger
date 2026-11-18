// soundcloud.js - the manifest's one static content script, on https://soundcloud.com/* top frames only.
// Add "Import to DJ digger" to playlist headers and tiles with their own playlist
// permalink. Only a trusted click hands a link to the background for import.
"use strict";
const DJD_LABEL = "Import to DJ digger";
// icons/icon-32.png (a test keeps them equal): with no web_accessible_resources the page cannot load the file
const DJD_ICON = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAAA7EAAAOxAGVKw4bAAAHZ0lEQVRYha2XbUyU2RWAn7kzwDAOOzIiwyKoGV0+i0vMhmz9jFEoYgglJaCN2LjGcROjxJgVdIMfCTTGNdFgjImm7S66lpgmS8GUilZNKmpSnFQDdflSEEEGi+wgzg4s73v7w5lhhhm0TXr+TO69Z+5z3nPOPfdcDX7y6W96YwTqXq1U8oVUlwqpGIVU0UoVIRWE51fL9DhozX+Md6yOC6l0C6k2aqVSc6qp9N9epsYPXiBQv9FKxSTkOwD/O9xPX0FI1Smksu2ra581+AzwwL/TSkXzPrhG/Ykpdy8m4490dnQgkBgiojHqzcyLSkQL74J7DZUCtfC3zbY/azxu737fl//w8gHPnjTieNFCeLjK6dOn2bFjh38E0Yd/wELLJ6QmrmOBOWU2OOKtF51Cqkt1npjPCnc5e/j+YQ1Dg3d8oIkJ0Ol0REZGotFocLlcALgnx+jsv0ln/00Wxy5nVcoW5s+JCwVHSNUkpLJXeBIuJHzo2TX+3lwaAPfK8PAwCxcu5Ny5c0FrAL3DdupavqT7xf1QcLRv5/KFJ9sD4FpUnj7+GvvdQyjKREiAw+EgISEBt9sdch1gSpmk8Z9naX36lyC456OXiFBHLXulE81PfwvasKSkBLPZDMDt27dRFCXAAIPBwMqVK4P+d6vzT8jYkaB8EFKNEqFivr9sI8XFxRQWFgZs9Pz5c0pLSwFobW2lr68vwIA9e/YEeUSv13PmzBku3TrHq/FBgsI9M9u/f1iDokxw9OhR9Ho9FRUVvs1aWlpYunQpFovlbZx7e6msrAQgNTUVg8HAgwcPfPrJycmcP3+eqqoqIufoWZw3LxCOgvajn9mO+h+1fz2aTqq2tjZcLhdWq5X+/n4A3G43q1evprW1FQBFUQDYt28ftbW1vHr1CoDs7Gzy8/M5ePAgNpuNrKwsvv72D8ydMhETYULw1hCdf5F59qQxKH52ux2dTjcdz1u3yM3NJS8vj4yMDADGxsYYHR2lq6sLgJ07dwJQXV1NWloaN27c8HnGGGckyZTo87jOv8I5XrT4QFqtlpSUFNrb25mamvLNx8XFkZmZyYEDBwIMbW5u5tKlS+zevRu73U58fDzJycnY7faAkCQt+wgcUwgpPTngOR7jP3QzMeH0KUspKSoq4sKFC2zfvp2kpCQ0Gg21tbXk5OQEeSonJ4dr167x5s0bcnNzaWtr4+HDh6xdu5bKykrq6urYunUrqlYy/GbIl/g6b1JMuBwAaDQa4uLicLlcVFVVYTQaWbFiBdu2bSMsLIzs7OwguFeWLVvGxYsX2bVrV4An6+vrqa6uRlVVAD5P/zVCb57OAa1Ucf/4EoCwsDAqKiowGAwYjUampqaw2Ww0NTVx+PDhWeFeEUIEjG/evBmk83pibDoHfMcQCcDk5CRlZWU+5YiICCYnJwGYmAhdFf3FarVSXl7O8PAwDocDh8NBR0cH4+PjPh2NlL7y7AuBISI65Ib+UIfD8V4DGhoacDgcxMbGYrFYyMjIQK/X09IyneDRYZG+69p3DI16c8gNhRDk5eWxadMmOjs7aW9vJz09PaTu9evXycrKora2NqAgzRRz+Jzpiuh1xbyoRPThHwQo5uXlcfnyZcxmMw0NDSQkJLBhwwaam5uDNr1z5w6HDh3iypUr2Gw2ysrKiI4O9mpUmIFE/bzp7mr6BoSFlk8ClF++fMnmzZu5d+8eycnJ7N+/n6GhIXJzc1m3bh3l5eWcPXuWwsJC1qxZQ1FREW63m4qKClpbWzl+/Ljv7vDKx3Ot6FB9rZ12efKWo94ezhA2h47+Wz7lwcFB1q9fz+joKE1NTQEb9fb20tLSgsViwel00tXVxd27dzlx4gT19fX09/dz9epVFi1aRFVVFS6Xi66uLrYuWktshHH6/vFvIBeYU1gcuxw89eDUqVN0d3fT09MzazwHBweJj48HwOVyUV1dTUFBgW+9sbGR4uJiYmJiqDn2FalRHwZcfmJmA7kqZQtzTWbq6uo4efIkfX19s8JnGgDQ1dXF/fv3g+L/x4vfovy1M7iLntm9zp8Th63wS0pKShgYGAgJNZlMvgtnpgF4jmtlZSUJCQm+uV3WbGKnwme08G9DMD6zVZIvolmXVDTrV6elpWE0GgEYGRnBaDQSERERoHPs2DGOHDkCwObEVXwabQ2GS+W18LxYglrnny/O4VfLdqITYUEGWK1Wnjx5MmsYAJxOJ787f4GLX5wkP+7jUHCEVHuE57kUsnVOj83k86wDpMSkv9OAgYEBFixYEKCTOddKoTsJcfvpbHC0UmnUaaVSI6S6V0jFFKp1thjmU5rxGX2j3diH/sHjkcezesCoiyQzegmrY1JJm5HtIeBOgVqjAfjiF78v0Er1O4GqCfWa8e/hNCi81o0xNDTA64kxNFLyYbSZuOgYTE5NQJF5B1wK1F9ufFTX4HucHso5XyCk8o2Qqmk2eMhH6AzAfwF3CtRtGx/VTT9OvXJ4w9kYIZW9WqnmC6ksEVKN+j/BXwup9mil0iBQazY+qhvxMv8D5EcYuo1P2EMAAAAASUVORK5CYII=";
const DJD_PAGES = new Set(["discover", "feed", "stream", "you", "search", "upload", "charts", "settings", "messages",
  "notifications", "pages", "signin", "logout", "people", "tags", "terms-of-use", "pro", "mobile", "jobs", "imprint"]);
const DJD_SET = /^\/([A-Za-z0-9_-]+)\/sets\/([A-Za-z0-9_-]+)\/?$/;
const DJD_SYSTEM_SET = /^\/discover\/sets\/(new-for-you|weekly)(?::|%3[aA])(?::|%3[aA])([A-Za-z0-9_-]+)\/?$/;
const DJD_HEADER = ".listenEngagement__actions .sc-button-group";
const DJD_TILE = ".playableTile";
const DJD_ROW = ".trackItem, .trackItem__actions, .sound__soundActions";

// Canonical playlist link from a page header or a tile's own artwork anchor.
function djdTarget(href) {
  if (typeof href !== "string" || href.length > 512) return null;
  let u;
  try {
    u = new URL(href, "https://soundcloud.com/");
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.hostname !== "soundcloud.com" || u.port || u.username || u.password) return null;
  const system = DJD_SYSTEM_SET.exec(u.pathname);
  if (system) return `https://soundcloud.com/discover/sets/${system[1]}::${system[2]}`;
  const set = DJD_SET.exec(u.pathname);
  return set && !DJD_PAGES.has(set[1].toLowerCase()) ? `https://soundcloud.com/${set[1]}/sets/${set[2]}` : null;
}

function djdTileAnchor(tile) {
  if (!tile.matches(DJD_TILE) || tile.closest(DJD_ROW) || tile.closest("a[href]") ||
      tile.parentElement?.closest(DJD_TILE)) return null;
  const links = [...tile.querySelectorAll("a.playableTile__artworkLink[href]")].filter(
    (link) => link.closest(DJD_TILE) === tile && !link.closest(DJD_ROW),
  );
  return links.length === 1 ? links[0] : null;
}

if (globalThis.document && window.top === window) {
  const api = globalThis.browser ?? globalThis.chrome;
  const buttons = new Map();
  const STYLE = /^sc-button(-secondary|-tertiary|-small|-medium|-large|-icon|-responsive)?$/;
  const show = (b, label, text) => {
    label.textContent = b.title = text;
    b.setAttribute("aria-label", text);
  };
  async function send(b, label, url, tilePage) {
    b.disabled = true;
    show(b, label, "Opening or sending to dj-digger…");
    let answer = null;
    try {
      answer = await api.runtime.sendMessage({ op: "import", url, ...(tilePage === undefined ? {} : { tilePage }) });
    } catch {
      // the extension was reloaded or updated under this page
    }
    const messages = {
      accepted: "Importing in dj-digger…", sent: "Sent to dj-digger", busy: "dj-digger is busy. Try again.",
      missing: "Install dj-digger and start it once.", update: "Update dj-digger to use this import.",
      "launch-failed": "Could not start dj-digger.", timeout: "dj-digger did not respond. Try again.",
    };
    show(b, label, messages[answer?.code] ?? "Could not send to dj-digger. Try again.");
    setTimeout(() => {
      show(b, label, DJD_LABEL);
      b.disabled = false;
    }, 3000);
  }
  function add(holder, ref, place, url, matches, tilePage) {
    if (!url || buttons.has(holder)) return;
    const page = location.href;
    const b = document.createElement("button");
    b.type = "button";
    const classes = ref ? [...ref.classList].filter((c) => STYLE.test(c)) : ["sc-button", "sc-button-secondary", "sc-button-small"];
    b.className = ["djd-import", ...classes].join(" ");
    b.title = DJD_LABEL;
    b.setAttribute("aria-label", DJD_LABEL);
    const icon = Object.assign(document.createElement("img"), { src: DJD_ICON, alt: "" });
    icon.style.cssText = "width:16px;height:16px;vertical-align:middle;border-radius:3px";
    const box = document.createElement("div");
    box.append(icon);
    const label = Object.assign(document.createElement("span"), { textContent: DJD_LABEL });
    label.className = ref?.querySelector(".sc-button-label")?.className || "sc-button-label";
    b.append(box, label);
    const valid = () => b.isConnected && b.parentElement === holder && location.href === page && matches();
    b.addEventListener("click", (e) => {
      if (!e.isTrusted) return; // the page must not start a desktop app by clicking our button
      e.preventDefault();
      e.stopPropagation();
      if (!b.disabled && valid()) send(b, label, url, tilePage);
    });
    place(b);
    buttons.set(holder, { b, valid });
  }
  function scan() {
    queued = false;
    for (const [holder, { b, valid }] of buttons) {
      if (!valid()) {
        b.remove();
        buttons.delete(holder);
      }
    }
    const target = djdTarget(location.href);
    for (const g of document.querySelectorAll(DJD_HEADER)) {
      const header = () => g.matches(DJD_HEADER) && !g.closest(`${DJD_TILE}, ${DJD_ROW}`);
      if (!target || !header() || buttons.has(g)) continue;
      const after = g.querySelector(":scope > .sc-button-addtoset");
      const more = g.querySelector(":scope > .sc-button-more");
      add(g, after ?? more ?? g.querySelector(":scope > button"),
        (b) => (after ? after.after(b) : g.insertBefore(b, more)), target, header);
    }
    for (const tile of document.querySelectorAll(DJD_TILE)) {
      if (buttons.has(tile)) continue;
      const anchor = djdTileAnchor(tile);
      const url = djdTarget(anchor?.getAttribute("href"));
      add(tile, null, (b) => tile.append(b), url,
        () => djdTileAnchor(tile) === anchor && djdTarget(anchor.getAttribute("href")) === url, location.pathname);
    }
  }
  // SoundCloud is a single-page app: one observer, at most one scan a frame, each one idempotent.
  let queued = false;
  new MutationObserver(() => {
    if (!queued) queued = !!requestAnimationFrame(scan);
  }).observe(document.body, { childList: true, attributes: true, attributeFilter: ["class", "href"], subtree: true });
  scan();
}
