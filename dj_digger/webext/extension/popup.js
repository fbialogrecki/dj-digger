// popup.js - the toolbar panel: the desktop app's player as its record, with previous, play/pause, next and
// a seek bar; the app's running job with Cancel; the gates waiting for a person; and the import of the
// SoundCloud set or profile this panel was opened over. It reads what the background keeps in
// storage.session (djdPlayer: the app's checked player.state, null while the app is gone) and asks the
// background, which alone talks to the app and checks every ask again.
"use strict";
const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
// Main.qml's default pair (magenta-blue) per theme: rest foot, rest top, neon foot, neon top.
const DEFAULT_PAIR = { dark: ["#b8286a", "#3a5fd6", "#ff2fa8", "#1e9bff"], light: ["#ff2187", "#4d77ff", "#ff0094", "#42b1ff"] };
const PLAY = "M7 4 L21 12 L7 20 Z";
const PAUSE = "M6 4 H10 V20 H6 Z M14 4 H18 V20 H14 Z";
const EMPTY_SEED = 7; // cover.hash of no track

// root.seeded() in Main.qml (mulberry32): the same seed always draws the same record.
function seeded(state) {
  state |= 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function canvas2d(el) {
  const size = el.clientWidth, ratio = devicePixelRatio || 1;
  el.width = el.height = Math.round(size * ratio);
  const ctx = el.getContext("2d");
  ctx.scale(ratio, ratio);
  return [ctx, size];
}
// Backdrop in Main.qml: the two colours blended along a seeded line, one pooled around a seeded spot.
function drawBackdrop(seed, first, second) {
  const [ctx, w] = canvas2d($("backdrop"));
  const random = seeded(seed);
  const angle = random() * Math.PI * 2, dx = (Math.cos(angle) * w) / 2, dy = (Math.sin(angle) * w) / 2;
  const line = ctx.createLinearGradient(w / 2 - dx, w / 2 - dy, w / 2 + dx, w / 2 + dy);
  line.addColorStop(random() * 0.35, first);
  line.addColorStop(0.65 + random() * 0.35, second);
  ctx.fillStyle = line;
  ctx.fillRect(0, 0, w, w);
  const pool = random() < 0.5 ? first : second, x = random() * w, y = random() * w;
  const spot = ctx.createRadialGradient(x, y, 0, x, y, w * (0.3 + random() * 0.4));
  spot.addColorStop(0, pool);
  spot.addColorStop(1, pool + "00");
  ctx.fillStyle = spot;
  ctx.fillRect(0, 0, w, w);
}
// The trackCode canvas in Main.qml: radial strokes and dots from the label to the rim.
function drawCode(seed) {
  const [ctx, w] = canvas2d($("code"));
  const random = seeded(seed);
  const centre = w / 2, inner = w * (0.42 / 2 + 0.02), outer = w * 0.48;
  const patterns = [[[0, 1]], [[0, 0.5]], [[0.5, 1]], [[0.3, 0.7]], [[0, 1 / 3], [2 / 3, 1]], []];
  ctx.strokeStyle = ctx.fillStyle = "rgba(255, 255, 255, 0.6)";
  ctx.lineWidth = Math.max(1, w / 110);
  ctx.lineCap = "round";
  const count = 18 + Math.floor(random() * 10), turn = random() * Math.PI * 2;
  for (let i = 0; i < count; i++) {
    const angle = turn + ((i + 0.15 + random() * 0.7) / count) * Math.PI * 2, dx = Math.cos(angle), dy = Math.sin(angle);
    const pattern = patterns[Math.floor(random() * patterns.length)];
    for (const [a, b] of pattern) {
      const from = inner + a * (outer - inner), to = inner + b * (outer - inner);
      ctx.beginPath();
      ctx.moveTo(centre + dx * from, centre + dy * from);
      ctx.lineTo(centre + dx * to, centre + dy * to);
      ctx.stroke();
    }
    if (pattern.length === 0 || random() < 0.3) {
      for (let dots = 1 + Math.floor(random() * 3); dots > 0; dots--) {
        const r = inner + random() * (outer - inner);
        ctx.beginPath();
        ctx.arc(centre + dx * r, centre + dy * r, ctx.lineWidth * 1.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
}
// Qt.darker(colour, 1.25): the HSV value over 1.25, i.e. every channel times 0.8.
const darker = (hex) =>
  "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * 0.8).toString(16).padStart(2, "0")).join("");

const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
// The track time now: the app's position runs on from when it was read while playing.
const elapsed = (s) => Math.min(s.duration, s.position + (s.playing && s.at ? Math.max(0, Date.now() / 1000 - s.at) : 0));

let state = null;
let dragging = false; // the seek bar is in the person's hand: the clock leaves it alone
let offer = null; // "set" or "profile": what the active tab's page can be imported as
let sent = false;
function tickSeek() {
  const on = !!state?.title && state.duration > 0;
  $("seek").disabled = !on;
  if (dragging) return;
  const t = on ? elapsed(state) : 0;
  $("seek").value = on ? Math.round((t / state.duration) * 1000) : 0;
  $("elapsed").textContent = clock(t);
  $("total").textContent = clock(on ? state.duration : 0);
}

let drawn = "";
function render(s) {
  state = s;
  // The cards: gates waiting for a person, the import this page offers, the running job.
  const needs = s?.needs ?? 0;
  $("gates").hidden = !needs;
  $("gatesText").textContent = needs === 1 ? "1 gate needs you" : `${needs} gates need you`;
  $("import").hidden = !s || !offer;
  $("importButton").textContent = offer === "set" ? "Import this playlist into dj-digger" : "Import all playlists of this profile";
  $("importButton").hidden = sent;
  $("importDone").hidden = !sent;
  const job = s?.jobs;
  $("job").hidden = !job?.busy;
  if (job?.busy) {
    const label = job.label || "Working…";
    $("jobLabel").textContent = job.total ? `${label} ${job.done} / ${job.total}` : label;
    $("jobBar").hidden = !job.total;
    $("jobBar").value = job.total ? Math.min(1, job.done / job.total) : 0;
  }
  $("panel").classList.toggle("extra", !$("gates").hidden || !$("import").hidden || !$("job").hidden);
  tickSeek();
  const loaded = !!s?.title;
  const dark = s ? s.dark : matchMedia("(prefers-color-scheme: dark)").matches;
  const pair = s?.colors ?? DEFAULT_PAIR[dark ? "dark" : "light"];
  const seed = loaded ? s.seed : EMPTY_SEED;
  const root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  pair.forEach((c, i) => root.style.setProperty(`--c${i + 1}`, c));
  root.style.setProperty("--label", darker((seed >> 5) % 2 ? pair[0] : pair[1]));
  const look = `${seed}|${pair}|${$("backdrop").clientWidth}`; // the record shrinks while a card shows
  if (look !== drawn) {
    drawn = look;
    drawBackdrop(seed, pair[0], pair[1]);
    drawCode(seed);
  }
  const art = $("art");
  const picture = loaded ? s.artwork : "";
  if (art.getAttribute("src") !== picture) {
    art.classList.remove("ready");
    if (picture) art.src = picture;
    else art.removeAttribute("src");
  }
  $("panel").classList.toggle("idle", !loaded);
  $("panel").classList.toggle("playing", loaded && s.playing);
  $("title").textContent = loaded ? s.title : s ? "Nothing playing" : "Open dj-digger to control playback";
  $("title").title = loaded ? s.title : "";
  $("artist").textContent = loaded ? s.artist : "";
  const verb = loaded && s.playing ? "Pause" : "Play";
  $("toggleIcon").setAttribute("d", verb === "Pause" ? PAUSE : PLAY);
  $("toggle").setAttribute("aria-label", verb);
  $("toggle").title = verb;
  for (const id of ["prev", "toggle", "next"]) $(id).disabled = !loaded;
}

const ask = (m) => Promise.resolve(api.runtime.sendMessage(m)).catch(() => {});
$("art").addEventListener("load", () => $("art").classList.add("ready"));
for (const op of ["prev", "toggle", "next", "cancel"]) $(op).addEventListener("click", () => ask({ op }));
$("gatesOpen").addEventListener("click", () => ask({ op: "gates", choice: "open" }));
$("gatesSkip").addEventListener("click", () => ask({ op: "gates", choice: "skip" }));
$("seek").addEventListener("input", () => {
  dragging = true;
  $("elapsed").textContent = clock((state?.duration ?? 0) * ($("seek").value / 1000));
});
$("seek").addEventListener("change", () => {
  const fraction = $("seek").value / 1000;
  dragging = false;
  if (state?.duration) state = { ...state, position: fraction * state.duration, at: Date.now() / 1000 }; // until the app says
  ask({ op: "seek", fraction });
});
setInterval(tickSeek, 250);
api.storage.session.onChanged.addListener((changes) => changes.djdPlayer && render(changes.djdPlayer.newValue));
render(null);
api.storage.session.get("djdPlayer").then(({ djdPlayer }) => render(djdPlayer));

// The page this panel was opened over: a SoundCloud set or profile can be imported. The URL is read only
// now (the activeTab grant of this click) and only the background decides what it is.
api.tabs.query({ active: true, currentWindow: true }).then(async ([tab]) => {
  const url = tab?.url ?? "";
  offer = url ? (await ask({ op: "page", url }))?.kind ?? null : null;
  $("importButton").onclick = () => {
    sent = true;
    ask({ op: "import", url });
    render(state);
  };
  render(state);
}, () => {});
