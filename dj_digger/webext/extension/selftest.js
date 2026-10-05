// selftest.js - manual self-test of dom.js on a synthetic page (plan D8/W15).
// Open selftest.html from the extension (chrome-extension://<id>/selftest.html or the
// moz-extension URL from about:debugging); every line must say PASS. It sends no commands.
"use strict";

const fixture = document.getElementById("fixture");
const results = document.getElementById("results");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let deny;

const set = (html) => {
  fixture.innerHTML = html;
};
// Every locator is scoped to the fixture, so the report below never matches.
const run = (op, locator, extra = {}) =>
  djDom.run({ op, locator: [{ css: "#fixture" }, ...locator], deny, timeoutMs: 1000, ...extra });
const value = async (op, locator, extra) => {
  const r = await run(op, locator, extra);
  if (!r.ok) throw new Error(`${r.code}: ${r.message}`);
  return r.result;
};
const same = (got, want) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
};

const cases = {
  async "css and :visible suffix"() {
    set('<a class="x" style="display:none">hidden</a><a class="x">shown</a>');
    same(await value("count", [{ css: "a.x" }]), 2);
    same(await value("count", [{ css: "a.x:visible" }]), 1);
  },
  async "role: implicit roles, regex name, exact, hidden skipped"() {
    set(`<a href="#">Buy Now thing</a><a>no href</a><button>Go</button>
      <input type="submit" value="Send it"><button style="display:none">Ghost</button>
      <div role="button" aria-label="Custom">x</div>
      <span id="lbl">Labelled</span><button aria-labelledby="lbl"></button>`);
    same(await value("count", [{ role: "link" }]), 1);
    same(await value("count", [{ role: "link", name: "^buy", flags: "i" }]), 1);
    same(await value("count", [{ role: "button", name: "send it" }]), 1);
    same(await value("count", [{ role: "button", name: "go", exact: true }]), 0);
    same(await value("count", [{ role: "button", name: "Go", exact: true }]), 1);
    same(await value("count", [{ role: "button", name: "Custom" }]), 1);
    same(await value("count", [{ role: "button", name: "Labelled" }]), 1);
    same(await value("count", [{ role: "button", name: "Ghost" }]), 0);
    same(await value("count", [{ text: "Ghost" }]), 1); // text does not filter hidden
  },
  async "text: whitespace-normalised, deepest element"() {
    set("<p>  Hello   <b>wor</b>ld </p><p>other</p>");
    same(await value("count", [{ text: "hello world" }]), 1);
    same(await value("attr", [{ text: "hello world" }], { name: "id" }), null);
    same(await value("count", [{ text: "Hello world", exact: true }]), 1);
    same(await value("count", [{ text: "^other$", flags: "" }]), 1);
  },
  async "placeholder"() {
    set('<input placeholder="Search for artist"><input placeholder="Other">');
    same(await value("count", [{ placeholder: "search for" }]), 1);
  },
  async "nth, first, negative nth, chained scope, xpath"() {
    set(`<ul><li class="item">one<a href="/a">A</a></li><li class="item">two<a href="/b">B</a></li>
      <li class="item">three</li></ul><h4><button id="b1">x</button></h4>`);
    same(await value("text", [{ css: ".item" }, { first: true }]), "oneA");
    same(await value("text", [{ css: ".item" }, { nth: 2 }]), "three");
    same(await value("text", [{ css: ".item" }, { nth: -1 }]), "three");
    same(await value("attr", [{ css: ".item" }, { nth: 1 }, { css: "a[href]" }], { name: "href" }), "/b");
    same(await value("count", [{ css: "#b1" }, { xpath: "ancestor::h4[1]" }]), 1);
  },
  async "waiting for an element that appears after 300 ms"() {
    set("");
    setTimeout(() => {
      fixture.insertAdjacentHTML("beforeend", '<button id="late">Late</button>');
    }, 300);
    const t0 = performance.now();
    same(await value("text", [{ css: "#late" }], { timeoutMs: 3000 }), "Late");
    if (performance.now() - t0 < 250) throw new Error("did not wait");
  },
  async "timeout and count/visible answer at once"() {
    set("");
    const r = await run("attr", [{ css: "#nope" }], { name: "id", timeoutMs: 200 });
    same([r.ok, r.code], [false, "timeout"]);
    const t0 = performance.now();
    same(await value("count", [{ css: "#nope" }]), 0);
    same(await value("visible", [{ css: "#nope" }]), false);
    if (performance.now() - t0 > 150) throw new Error("count/visible waited");
  },
  async "waitVisible"() {
    set('<button id="b" style="display:none">B</button>');
    setTimeout(() => {
      document.getElementById("b").style.display = "";
    }, 300);
    same(await value("waitVisible", [{ css: "#b" }], { timeoutMs: 3000 }), true);
  },
  async "cancel stops a wait"() {
    set("");
    const pending = run("click", [{ css: "#never" }], { timeoutMs: 5000, token: "selftest" });
    setTimeout(() => (globalThis.__djdCancel ||= new Set()).add("selftest"), 100);
    const t0 = performance.now();
    const r = await pending;
    same([r.ok, r.code], [false, "cancelled"]);
    if (performance.now() - t0 > 1000) throw new Error("cancel was slow");
  },
  async "click event sequence"() {
    set('<button id="b">Press</button>');
    const seen = [];
    const b = document.getElementById("b");
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      b.addEventListener(t, (e) => seen.push(e.type));
    }
    same(await value("click", [{ css: "#b" }]), true);
    same(seen, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
  },
  async "click waits for a disabled button to be enabled"() {
    set('<button id="b" disabled>Later</button>');
    let clicks = 0;
    document.getElementById("b").addEventListener("click", () => clicks++);
    setTimeout(() => (document.getElementById("b").disabled = false), 300);
    await value("click", [{ css: "#b" }], { timeoutMs: 3000 });
    same(clicks, 1);
  },
  async "fill on a React-like controlled input"() {
    set('<input id="i"><textarea id="t"></textarea>');
    const input = document.getElementById("i");
    // React keeps its own tracker behind an instance `value` property and only reacts to an
    // input event when the node's value differs from what the tracker last saw.
    const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    let tracked = "";
    Object.defineProperty(input, "value", {
      configurable: true,
      get() { return native.get.call(this); },
      set(v) { tracked = String(v); native.set.call(this, v); },
    });
    const events = [];
    input.addEventListener("input", () => {
      if (input.value !== tracked) {
        events.push("changed:" + input.value);
        tracked = input.value;
      }
    });
    input.addEventListener("change", () => events.push("change"));
    same(await value("fill", [{ css: "#i" }], { value: "abc" }), true);
    same(events, ["changed:abc", "change"]);
    same(await value("value", [{ css: "#i" }]), "abc");
    await value("fill", [{ css: "#t" }], { value: "multi" });
    same(await value("value", [{ css: "#t" }]), "multi");
  },
  async "fill refuses password and card fields"() {
    set('<input id="p" type="password"><input id="c" autocomplete="cc-number">');
    for (const id of ["p", "c"]) {
      const r = await run("fill", [{ css: "#" + id }], { value: "x" });
      same([r.ok, r.code], [false, "denied"]);
    }
  },
  async "dom.all returns attributes and text"() {
    set('<a class="r" href="/1" title="t1">One</a><a class="r" href="/2">Two</a><a class="r" href="/3">Three</a>');
    same(await value("all", [{ css: "a.r" }], { attrs: ["href", "title"], text: true, limit: 2 }), [
      { attrs: { href: "/1", title: "t1" }, text: "One" },
      { attrs: { href: "/2", title: null }, text: "Two" },
    ]);
    same((await value("all", [{ css: "a.r" }], { attrs: ["href"] }))[0].text, "");
  },
  async "click denylist refuses checkout and payment controls"() {
    set(`<button class="d">Checkout</button><button class="d">Pay now</button>
      <button class="d"><span>Place order</span></button><a class="d" href="#" title="Purchase">x</a>
      <button class="ok">Add to cart</button>`);
    let clicks = 0;
    fixture.addEventListener("click", () => clicks++);
    for (let i = 0; i < 4; i++) {
      const r = await run("click", [{ css: ".d" }, { nth: i }]);
      same([r.ok, r.code], [false, "denied"]);
    }
    same(clicks, 0);
    same(await value("click", [{ css: ".ok" }]), true);
    same(clicks, 1);
  },
  async "denylist exception applies only to its host"() {
    set('<button id="b">Buy now</button>');
    let clicks = 0;
    document.getElementById("b").addEventListener("click", () => clicks++);
    same((await run("click", [{ css: "#b" }])).code, "denied");
    const exception = (host) => ({ ...deny, exceptions: [{ host, pattern: "^buy now$", flags: "i" }] });
    same((await run("click", [{ css: "#b" }], { deny: exception("example.org") })).code, "denied");
    same(await value("click", [{ css: "#b" }], { deny: exception(location.hostname) }), true);
    same(clicks, 1);
    same((await run("click", [{ css: "#b" }], { deny: undefined })).code, "denied"); // fails closed
  },
  async "bandcamp MAIN-world readers"() {
    globalThis.TralbumData = { artist: "A", current: { track_id: 7, title: "T", minimum_price: 1.5, currency: "EUR", set_price: 2 } };
    same(djDom.main.bandcampTralbum(), { id: "7", title: "T", artist: "A", minimum: 1.5, currency: "EUR" });
    same(djDom.main.bandcampSuggestedPrice(), 2);
    delete globalThis.TralbumData;
    same(djDom.main.bandcampSuggestedPrice(), null);
  },
  async "html reports truncation"() {
    set("<p>x</p>");
    same(djDom.html(10).truncated, true);
    same(djDom.html(10_000_000).truncated, false);
  },
};

async function main() {
  deny = (await (await fetch("allowlist.json")).json()).clickDenylist;
  let failed = 0;
  for (const [name, fn] of Object.entries(cases)) {
    let line;
    try {
      await fn();
      line = ["pass", `PASS ${name}`];
    } catch (e) {
      failed++;
      line = ["fail", `FAIL ${name}: ${e.message}`];
    }
    const div = document.createElement("div");
    div.className = line[0];
    div.textContent = line[1];
    results.append(div);
    await wait(0);
  }
  set("");
  const summary = document.createElement("div");
  summary.className = failed ? "fail" : "pass";
  summary.textContent = failed ? `${failed} FAILED` : "ALL PASS";
  results.append(summary);
}
main();
