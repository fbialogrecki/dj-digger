// dom.js - functions injected with scripting.executeScript (and run directly by selftest.js).
// executeScript serialises `func` with toString(), so every function below must be
// self-contained: no reference to anything outside its own body.
//
// djRun(spec) is the whole locator engine. spec = {op, locator, name, value, attrs, text,
// limit, timeoutMs, deny, token}; it resolves to {ok: true, result} or {ok: false, code, message}.
// A locator is a list of steps, each narrowing the previous result like Playwright's chained
// locators: {css} {xpath} {role,name,flags,exact} {text,flags,exact} {placeholder,flags,exact}
// {nth} {first}. A string `name`/`text` is a case-insensitive substring ("exact": a
// case-sensitive whole-string match); when `flags` is a string, it is a RegExp source instead.
(function () {
  async function djRun(spec) {
    const err = (code, message) => Object.assign(new Error(message), { code });
    const norm = (s) => String(s ?? "").replace(/\s+/g, " ").trim();
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const cancelled = () => globalThis.__djdCancel?.has(spec.token);
    const deadline = Date.now() + (spec.timeoutMs ?? 0);

    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
    };
    const matcher = (pattern, flags, exact) => {
      if (typeof flags === "string") {
        const re = new RegExp(pattern, flags.replace(/[gy]/g, ""));
        return (s) => re.test(norm(s));
      }
      const want = norm(pattern);
      return exact
        ? (s) => norm(s) === want
        : (s) => norm(s).toLowerCase().includes(want.toLowerCase());
    };

    // Accessible name and role: the small subset the Python side asks for.
    const BUTTON_INPUTS = ["button", "submit", "reset", "image"];
    const implicitRole = (el) => {
      const tag = el.localName;
      if (tag === "a" || tag === "area") return el.hasAttribute("href") ? "link" : "";
      if (tag === "button") return "button";
      if (tag === "input" && BUTTON_INPUTS.includes((el.type || "").toLowerCase())) return "button";
      return "";
    };
    const roleOf = (el) =>
      (el.getAttribute("role") || "").trim().split(/\s+/)[0].toLowerCase() || implicitRole(el);
    const BLOCKISH = /^(block|flex|grid|list-item|table)/;
    const contentOf = (n) => {
      if (n.nodeType === 3) return n.data;
      if (n.nodeType !== 1 || ["script", "style"].includes(n.localName)) return "";
      if (n.localName === "img") return n.getAttribute("alt") || "";
      const display = getComputedStyle(n).display;
      if (display === "none") return "";
      const inner = [...n.childNodes].map(contentOf).join("");
      return BLOCKISH.test(display) ? ` ${inner} ` : inner;
    };
    const accName = (el) => {
      const ids = el.getAttribute("aria-labelledby");
      if (ids) {
        const t = norm(
          ids.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(contentOf).join(" ")
        );
        if (t) return t;
      }
      if (norm(el.getAttribute("aria-label"))) return norm(el.getAttribute("aria-label"));
      if (el.localName === "input") {
        const type = (el.type || "").toLowerCase();
        if (type === "image" && norm(el.alt)) return norm(el.alt);
        if (BUTTON_INPUTS.includes(type)) return norm(el.value);
      }
      if (el.localName === "img" && norm(el.alt)) return norm(el.alt);
      return norm(contentOf(el)) || norm(el.getAttribute("title"));
    };
    const hiddenForRole = (el) => !visible(el) || !!el.closest('[aria-hidden="true"]');

    // One locator step applied below one scope node.
    const SKIP_TEXT = ["script", "style", "noscript", "head", "meta", "title", "link"];
    const find = (scope, s) => {
      if ("css" in s) {
        let css = s.css;
        const trailing = /^([\s\S]*?)\s*:visible\s*$/.exec(css);
        if (trailing) css = trailing[1];
        // ponytail: ":visible" is supported only as a suffix and applies to the whole selector
        if (/:visible/.test(css)) throw err("bad_locator", ":visible is only supported as a suffix");
        const found = [...scope.querySelectorAll(css)];
        return trailing ? found.filter(visible) : found;
      }
      if ("xpath" in s) {
        const doc = scope.ownerDocument || scope;
        const snap = doc.evaluate(s.xpath, scope, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const found = [];
        for (let i = 0; i < snap.snapshotLength; i++) {
          if (snap.snapshotItem(i).nodeType === 1) found.push(snap.snapshotItem(i));
        }
        return found;
      }
      if ("role" in s) {
        const nameOk = "name" in s ? matcher(s.name, s.flags, s.exact) : () => true;
        return [...scope.querySelectorAll("a[href],area[href],button,input,[role]")].filter(
          (el) => roleOf(el) === s.role && !hiddenForRole(el) && nameOk(accName(el))
        );
      }
      if ("text" in s) {
        const ok = matcher(s.text, s.flags, s.exact);
        const hits = [...scope.querySelectorAll("*")].filter(
          (el) => !SKIP_TEXT.includes(el.localName) && ok(el.textContent)
        );
        // Like Playwright, report the deepest elements that carry the text.
        // ponytail: O(n*m) containment scan, fine for page-sized hit lists
        return hits.filter((el) => !hits.some((o) => o !== el && el.contains(o)));
      }
      if ("placeholder" in s) {
        const ok = matcher(s.placeholder, s.flags, s.exact);
        return [...scope.querySelectorAll("[placeholder]")].filter((el) => ok(el.getAttribute("placeholder")));
      }
      throw err("bad_locator", "unknown locator step");
    };
    const resolve = () => {
      let set = [document];
      for (const s of spec.locator || []) {
        if ("nth" in s || "first" in s) {
          const i = "first" in s ? 0 : s.nth < 0 ? set.length + s.nth : s.nth;
          set = set[i] ? [set[i]] : [];
          continue;
        }
        const next = new Set();
        for (const scope of set) for (const el of find(scope, s)) next.add(el);
        set = [...next].sort((a, b) => (a.compareDocumentPosition(b) & 4 ? -1 : 1));
      }
      return set.filter((n) => n.nodeType === 1);
    };

    const waitFor = async (probe) => {
      for (;;) {
        if (cancelled()) throw err("cancelled", "command cancelled");
        const v = probe();
        if (v) return v;
        if (Date.now() >= deadline) throw err("timeout", `timeout ${spec.timeoutMs}ms waiting for element`);
        await sleep(50);
      }
    };
    const first = () => resolve()[0];
    const enabled = (el) => !el.matches(":disabled") && el.getAttribute("aria-disabled") !== "true";
    const actionable = () => {
      const el = first();
      return el && visible(el) && enabled(el) ? el : null;
    };

    // Click denylist (plan section 4): the element and its nearest control must not look like
    // a checkout or payment control. Fails closed when the caller supplied no denylist.
    const checkDeny = (el) => {
      const d = spec.deny;
      if (!d) throw err("denied", "click refused: no denylist supplied");
      const re = new RegExp(d.pattern, d.flags);
      const host = location.hostname;
      const exempt = (name) =>
        (d.exceptions || []).some(
          (e) => (host === e.host || host.endsWith("." + e.host)) && new RegExp(e.pattern, e.flags).test(name)
        );
      const control = el.closest("a,button,[role=button],[role=link],input,label,summary");
      const names = [el, control].filter(Boolean).flatMap((n) => [
        accName(n), norm(n.innerText ?? n.textContent), norm(n.getAttribute("title")),
        norm(n.getAttribute("aria-label")), norm(n.value),
      ]);
      if (names.some((n) => re.test(n) && !exempt(n))) {
        throw err("denied", "click refused: control looks like checkout or payment");
      }
    };

    const fire = (el, type, init) => {
      const Ctor = type.startsWith("pointer") ? PointerEvent : MouseEvent;
      el.dispatchEvent(new Ctor(type, init));
    };
    const click = (el) => {
      checkDeny(el);
      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      const r = el.getBoundingClientRect();
      const x = r.left + r.width / 2;
      const y = r.top + r.height / 2;
      const base = {
        bubbles: true, cancelable: true, composed: true, view: window,
        clientX: x, clientY: y, screenX: x, screenY: y, button: 0, detail: 1,
        pointerId: 1, pointerType: "mouse", isPrimary: true,
      };
      fire(el, "pointerdown", { ...base, buttons: 1 });
      fire(el, "mousedown", { ...base, buttons: 1 });
      fire(el, "pointerup", { ...base, buttons: 0 });
      fire(el, "mouseup", { ...base, buttons: 0 });
      fire(el, "click", { ...base, buttons: 0 });
    };
    const fill = (el, value) => {
      const proto =
        el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
        : el instanceof HTMLInputElement ? HTMLInputElement.prototype
        : null;
      if (!proto) throw err("not_fillable", "element is not an input or textarea");
      // Credentials and card numbers are never typed by the extension.
      if ((el.type || "").toLowerCase() === "password" || /^cc-/.test(el.autocomplete || "")) {
        throw err("denied", "fill refused: password or payment field");
      }
      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      el.focus();
      // The prototype setter, not el.value=: React-style controlled inputs track the
      // instance property and would otherwise never notice the change.
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(value ?? ""));
      el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: String(value ?? "") }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };

    try {
      const ok = (result) => ({ ok: true, result });
      switch (spec.op) {
        case "count":
          return ok(resolve().length);
        case "visible": {
          const el = first();
          return ok(!!el && visible(el));
        }
        case "attr":
          return ok((await waitFor(first)).getAttribute(spec.name));
        case "text": {
          const el = await waitFor(first);
          return ok(el.innerText ?? el.textContent ?? "");
        }
        case "value": {
          const el = await waitFor(first);
          if (!("value" in el)) throw err("not_input", "element has no value");
          return ok(String(el.value));
        }
        case "all":
          return ok(
            resolve().slice(0, Math.min(spec.limit ?? 500, 500)).map((el) => ({
              attrs: Object.fromEntries((spec.attrs || []).map((a) => [a, el.getAttribute(a)])),
              text: spec.text ? (el.innerText ?? el.textContent ?? "") : "",
            }))
          );
        case "waitVisible":
          await waitFor(() => {
            const el = first();
            return el && visible(el);
          });
          return ok(true);
        case "click":
          click(await waitFor(actionable));
          return ok(true);
        case "fill":
          fill(await waitFor(() => { const el = actionable(); return el && !el.readOnly ? el : null; }), spec.value);
          return ok(true);
        default:
          throw err("bad_args", `unknown dom op ${spec.op}`);
      }
    } catch (e) {
      return { ok: false, code: e.code || "dom_error", message: String(e.message || e) };
    } finally {
      globalThis.__djdCancel?.delete(spec.token);
    }
  }

  // tab.html: the document, or nothing when it is larger than maxBytes (UTF-8).
  function djHtml(maxBytes) {
    const doctype = document.doctype ? new XMLSerializer().serializeToString(document.doctype) : "";
    const html = doctype + document.documentElement.outerHTML;
    return new TextEncoder().encode(html).length > maxBytes
      ? { html: "", truncated: true }
      : { html, truncated: false };
  }

  // MAIN-world, read-only. Same bodies as the page.evaluate calls in dj_digger/stores/bandcamp.py
  // (_bandcamp_dom_products and _bandcamp_quote_async). The page can replace globals, so the
  // results are untrusted data.
  function bandcampTralbum() {
    const t = globalThis.TralbumData || {};
    const current = t.current || {};
    return {
      id: String(current.track_id || current.id || ""),
      title: String(current.title || "").slice(0, 500),
      artist: String(current.artist || t.artist || "").slice(0, 500),
      minimum: current.minimum_price,
      currency: String(current.currency || t.currency || "").slice(0, 12),
    };
  }
  function bandcampSuggestedPrice() {
    return (globalThis.TralbumData || {}).current?.set_price ?? null;
  }

  globalThis.djDom = { run: djRun, html: djHtml, main: { bandcampTralbum, bandcampSuggestedPrice } };
})();
