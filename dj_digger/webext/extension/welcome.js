// welcome.js - status page. Reads state the background wrote to storage.session and can ask for
// the host permissions; it has no way to send a command.
"use strict";
const api = globalThis.browser ?? globalThis.chrome;
const origins = api.runtime.getManifest().host_permissions;
const $ = (id) => document.getElementById(id);

async function render() {
  const { djdStatus: s } = await api.storage.session.get("djdStatus");
  $("link").textContent = !s
    ? "not started yet"
    : s.connected
      ? "connected, waiting for the app"
      : s.missing
        ? "dj-digger desktop app not installed (or out of date)"
        : `not connected${s.error ? ` (${s.error})` : ""}. Is dj-digger installed and has it been started once?`;
  $("install").hidden = !s?.missing;
  const granted = await api.permissions.contains({ origins });
  $("access").textContent = granted ? "granted" : "missing";
  $("grant").hidden = granted;
}

$("grant").addEventListener("click", async () => {
  await api.permissions.request({ origins });
  render();
});
api.storage.session.onChanged.addListener(render);
api.permissions.onAdded.addListener(render);
api.permissions.onRemoved.addListener(render);
render();
