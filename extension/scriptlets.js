// Babelo — скриптлеты в духе uBlock Origin: небольшие скрипты, которые правила списков запускают на странице
// (##+js(имя, аргументы)). Реализованы самые востребованные; неизвестные пропускаются без ошибок.
(() => {
  "use strict";
  if (window.__babeloSL) return;
  const noop = () => {}, trueFn = () => true, falseFn = () => false;
  const toRe = (s) => {
    if (s === undefined || s === null || s === "") return { test: () => true };
    s = String(s);
    const m = s.match(/^\/(.+)\/([gimsu]*)$/);
    if (m) { try { return new RegExp(m[1], m[2].replace("g", "")); } catch (e) {} }
    return { test: (x) => String(x).includes(s) };
  };
  const parts = (chain) => String(chain).split(".");
  // Ждёт, пока в цепочке a.b.c появятся промежуточные объекты, и тогда вызывает fn(владелец, имя).
  function trap(owner, p, i, fn) {
    const prop = p[i];
    if (i === p.length - 1) { fn(owner, prop); return; }
    const cur = owner[prop];
    if (cur && (typeof cur === "object" || typeof cur === "function")) { trap(cur, p, i + 1, fn); return; }
    let stored = cur;
    try {
      Object.defineProperty(owner, prop, { configurable: true, enumerable: true,
        get() { return stored; },
        set(v) { stored = v; if (v && (typeof v === "object" || typeof v === "function")) trap(v, p, i + 1, fn); } });
    } catch (e) {}
  }
  const ready = (cb) => { if (document.readyState !== "loading") cb(); else document.addEventListener("DOMContentLoaded", cb, { once: true }); };
  const watchDom = (cb) => {
    let t = null;
    const run = () => { t = null; try { cb(); } catch (e) {} };
    ready(() => {
      run();
      if (document.documentElement) new MutationObserver(() => { if (!t) t = setTimeout(run, 250); })
        .observe(document.documentElement, { childList: true, subtree: true, attributes: true });
    });
  };
  const val = (raw) => {
    const m = { "undefined": undefined, "false": false, "true": true, "null": null, "noopFunc": noop, "trueFunc": trueFn,
      "falseFunc": falseFn, "throwFunc": () => { throw new Error("blocked"); }, "emptyStr": "", "''": "", "emptyArr": [],
      "emptyObj": {}, "yes": "yes", "no": "no", "NaN": NaN };
    if (raw in m) return { v: m[raw] };
    if (/^-?\d+(\.\d+)?$/.test(raw)) return { v: Number(raw) };
    return null;
  };
  // ---- обход путей JSON: «a.b», «*» — любое свойство, «[]» — любой элемент массива
  const walk = (o, p, i, cb) => {
    if (o === null || typeof o !== "object") return;
    const k = p[i], last = i === p.length - 1;
    const keys = k === "*" ? Object.keys(o) : (k === "[]" || k === "[-]") ? (Array.isArray(o) ? o.map((_, n) => n) : []) : (k in o ? [k] : []);
    for (const key of keys) { if (last) cb(o, key); else walk(o[key], p, i + 1, cb); }
  };
  const has = (o, path) => { let f = false; walk(o, path.split("."), 0, () => { f = true; }); return f; };
  const prune = (o, paths, needles) => {
    if (!o || typeof o !== "object") return o;
    if (needles.length && !needles.every((n) => has(o, n))) return o;
    for (const p of paths) walk(o, p.split("."), 0, (obj, key) => { if (Array.isArray(obj)) obj.splice(key, 1); else delete obj[key]; });
    return o;
  };
  const props = (raw) => {                  // "url:needle method:POST" -> {url: re, method: re}
    const out = {};
    for (const tok of String(raw || "").split(/\s+/).filter(Boolean)) {
      const i = tok.indexOf(":");
      if (i > 0) out[tok.slice(0, i)] = toRe(tok.slice(i + 1)); else out.url = toRe(tok);
    }
    return out;
  };
  const S = {
    "abort-on-property-read": ([chain]) => abort(chain, "read"),
    "abort-on-property-write": ([chain]) => abort(chain, "write"),
    "abort-current-script": ([chain, needle]) => {
      const re = toRe(needle), me = document.currentScript, err = "ReferenceError_" + Math.random().toString(36).slice(2);
      const check = () => { const s = document.currentScript; if (s && s !== me && re.test((s.textContent || "") + " " + (s.src || ""))) throw new ReferenceError(err); };
      trap(window, parts(chain), 0, (o, p) => { let v = o[p]; try { Object.defineProperty(o, p, { configurable: true, enumerable: true,
        get() { check(); return v; }, set(x) { check(); v = x; } }); } catch (e) {} });
    },
    "abort-on-stack-trace": ([chain, needle]) => {
      const re = toRe(needle), err = "ReferenceError_" + Math.random().toString(36).slice(2);
      trap(window, parts(chain), 0, (o, p) => { let v = o[p]; try { Object.defineProperty(o, p, { configurable: true, enumerable: true,
        get() { if (re.test(new Error().stack)) throw new ReferenceError(err); return v; },
        set(x) { if (re.test(new Error().stack)) throw new ReferenceError(err); v = x; } }); } catch (e) {} });
    },
    "set-constant": ([chain, raw]) => {
      const r = val(raw); if (!r) return;
      trap(window, parts(chain), 0, (o, p) => { try { Object.defineProperty(o, p, { configurable: true, enumerable: true, get() { return r.v; }, set() {} }); } catch (e) {} });
    },
    "json-prune": ([paths, needles]) => {
      const P = String(paths || "").split(/\s+/).filter(Boolean), N = String(needles || "").split(/\s+/).filter(Boolean);
      const nativeParse = JSON.parse;
      JSON.parse = function () { const r = nativeParse.apply(this, arguments); try { prune(r, P, N); } catch (e) {} return r; };
    },
    "json-prune-fetch-response": ([paths, needles, propsRaw]) => {
      const P = String(paths || "").split(/\s+/).filter(Boolean), N = String(needles || "").split(/\s+/).filter(Boolean), pr = props(propsRaw);
      const nf = window.fetch;
      window.fetch = function () {
        const p = nf.apply(this, arguments), a0 = arguments[0], url = String((a0 && a0.url) || a0);
        if (pr.url && !pr.url.test(url)) return p;
        return p.then((r) => r.clone().json().then((j) => new Response(JSON.stringify(prune(j, P, N)),
          { status: r.status, statusText: r.statusText, headers: r.headers })).catch(() => r));
      };
    },
    "no-fetch-if": ([raw]) => {
      const pr = props(raw), nf = window.fetch;
      window.fetch = function () {
        const a0 = arguments[0], url = String((a0 && a0.url) || a0), method = String((arguments[1] && arguments[1].method) || (a0 && a0.method) || "GET");
        if ((!pr.url || pr.url.test(url)) && (!pr.method || pr.method.test(method))) return Promise.resolve(new Response("", { status: 200 }));
        return nf.apply(this, arguments);
      };
    },
    "no-xhr-if": ([raw]) => {
      const pr = props(raw), open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (m, u) { this.__bm = String(m); this.__bu = String(u); return open.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function () {
        if ((!pr.url || pr.url.test(this.__bu)) && (!pr.method || pr.method.test(this.__bm))) {
          const x = this;
          setTimeout(() => { for (const [k, v] of [["readyState", 4], ["status", 200], ["responseText", ""], ["response", ""]])
            try { Object.defineProperty(x, k, { value: v, configurable: true }); } catch (e) {}
            x.dispatchEvent(new Event("readystatechange")); x.dispatchEvent(new Event("load")); x.dispatchEvent(new Event("loadend")); }, 1);
          return;
        }
        return send.apply(this, arguments);
      };
    },
    "no-setTimeout-if": ([needle, delay]) => timer("setTimeout", needle, delay, null),
    "no-setInterval-if": ([needle, delay]) => timer("setInterval", needle, delay, null),
    "nano-setTimeout-booster": ([needle, delay, boost]) => timer("setTimeout", needle, delay, parseFloat(boost) || 0.05),
    "nano-setInterval-booster": ([needle, delay, boost]) => timer("setInterval", needle, delay, parseFloat(boost) || 0.05),
    "addEventListener-defuser": ([type, handler]) => {
      const tr = toRe(type), hr = toRe(handler), ael = EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener = function (t, h) {
        const src = typeof h === "function" ? Function.prototype.toString.call(h) : (h && h.handleEvent ? String(h.handleEvent) : "");
        if (tr.test(t) && hr.test(src)) return;
        return ael.apply(this, arguments);
      };
    },
    "window.open-defuser": ([needle]) => {
      const re = toRe(needle), wo = window.open;
      window.open = function (u) { if (re.test(String(u))) return null; return wo.apply(this, arguments); };
    },
    "remove-attr": ([attrs, selector]) => {
      const list = String(attrs || "").split("|").filter(Boolean), sel = selector || list.map((a) => "[" + a + "]").join(",");
      watchDom(() => document.querySelectorAll(sel).forEach((el) => list.forEach((a) => el.removeAttribute(a))));
    },
    "remove-class": ([classes, selector]) => {
      const list = String(classes || "").split("|").filter(Boolean), sel = selector || list.map((c) => "." + c).join(",");
      watchDom(() => document.querySelectorAll(sel).forEach((el) => list.forEach((c) => el.classList.remove(c))));
    },
    "set-attr": ([selector, attr, value]) => watchDom(() => document.querySelectorAll(selector).forEach((el) => el.setAttribute(attr, value || ""))),
    "set-cookie": ([name, value]) => { try { document.cookie = name + "=" + value + "; path=/; max-age=31536000"; } catch (e) {} },
    "set-local-storage-item": ([k, v]) => { try { localStorage.setItem(k, val(v) ? String(val(v).v) : v); } catch (e) {} },
    "set-session-storage-item": ([k, v]) => { try { sessionStorage.setItem(k, val(v) ? String(val(v).v) : v); } catch (e) {} },
    "disable-newtab-links": () => document.addEventListener("click", (e) => {
      const a = e.target && e.target.closest && e.target.closest('a[target="_blank"]'); if (a) a.target = "_self"; }, true),
    "alert-buster": () => { window.alert = noop; },
    "noeval": () => { window.eval = noop; },
    "href-sanitizer": ([selector, attr]) => watchDom(() => document.querySelectorAll(selector).forEach((a) => {
      let t = attr && attr !== "text" ? a.getAttribute(attr.replace(/^\[|\]$/g, "")) : a.textContent;
      try { const u = new URL(t, location.href); if (/^https?:$/.test(u.protocol)) a.href = u.href; } catch (e) {} })),
  };
  function abort(chain, mode) {
    const err = "ReferenceError_" + Math.random().toString(36).slice(2);
    trap(window, parts(chain), 0, (o, p) => { let v = o[p]; try { Object.defineProperty(o, p, { configurable: true, enumerable: true,
      get() { if (mode === "read") throw new ReferenceError(err); return v; },
      set(x) { if (mode === "write") throw new ReferenceError(err); v = x; } }); } catch (e) {} });
  }
  function timer(kind, needle, delay, boost) {
    const re = toRe(needle), dl = delay === undefined || delay === "" || delay === "*" ? null : parseInt(delay, 10), nat = window[kind];
    window[kind] = function (fn, d) {
      const src = typeof fn === "function" ? Function.prototype.toString.call(fn) : String(fn);
      if (re.test(src) && (dl === null || d === dl || (boost && dl === null))) {
        if (boost === null) return 0;                                   // no-…-if: вообще не запускаем
        const a = Array.prototype.slice.call(arguments); a[1] = (d || 0) * boost; return nat.apply(this, a);
      }
      return nat.apply(this, arguments);
    };
  }
  const ALIAS = { aopr: "abort-on-property-read", aopw: "abort-on-property-write", acs: "abort-current-script",
    acis: "abort-current-script", "abort-current-inline-script": "abort-current-script", aost: "abort-on-stack-trace",
    set: "set-constant", nostif: "no-setTimeout-if", "prevent-setTimeout": "no-setTimeout-if", nosiif: "no-setInterval-if",
    "prevent-setInterval": "no-setInterval-if", "nano-stb": "nano-setTimeout-booster", "nano-sib": "nano-setInterval-booster",
    aeld: "addEventListener-defuser", "ael-defuser": "addEventListener-defuser", "prevent-addEventListener": "addEventListener-defuser",
    nowoif: "window.open-defuser", "no-window-open-if": "window.open-defuser", "window-open-defuser": "window.open-defuser",
    ra: "remove-attr", rc: "remove-class", "prevent-fetch": "no-fetch-if", "prevent-xhr": "no-xhr-if", "no-xhr-if": "no-xhr-if",
    "disable-newtab-links": "disable-newtab-links" };
  window.__babeloSL = {
    names: Object.keys(S),
    run(name, args) {
      const n = ALIAS[name] || name, f = S[n];
      if (!f) return false;
      try { f(args || []); return true; } catch (e) { return false; }
    }
  };
})();
