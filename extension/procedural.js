// Babelo — «процедурные» правила скрытия uBlock/AdBlock: :has-text(), :matches-css(), :upward(), :xpath(), :remove(), :style() и др.
(() => {
  "use strict";
  if (window.__babeloProc) return;
  const OPS = ["has-text", "-abp-contains", "contains", "-abp-has", "has", "not", "matches-css-before", "matches-css-after", "matches-css",
    "matches-attr", "matches-media", "matches-path", "min-text-length", "upward", "nth-ancestor", "xpath", "watch-attr", "remove", "style"];
  const OPS_RE = /:(has-text|-abp-contains|contains|-abp-has|matches-css-before|matches-css-after|matches-css|matches-attr|matches-media|matches-path|min-text-length|upward|nth-ancestor|xpath|watch-attr|remove|style)\(/;

  // ---- разбор: «div.post:has-text(/Реклама/i) > a:upward(2):remove()» -> шаги
  function closeParen(s, i) {                       // s[i] — после «(»; возвращает индекс парной «)»
    let d = 1, q = null;
    for (; i < s.length; i++) {
      const c = s[i];
      if (q) { if (c === "\\") i++; else if (c === q) q = null; continue; }
      if (c === '"' || c === "'") { q = c; continue; }
      if (c === "(") d++; else if (c === ")") { d--; if (d === 0) return i; }
    }
    return -1;
  }
  function isProc(sel) { return OPS_RE.test(sel); }
  function parse(sel) {
    if (!isProc(sel)) return null;
    const steps = [];
    let buf = "", i = 0, q = null, br = 0;
    while (i < sel.length) {
      const c = sel[i];
      if (q) { buf += c; if (c === "\\") { buf += sel[++i] || ""; } else if (c === q) q = null; i++; continue; }
      if (c === '"' || c === "'") { q = c; buf += c; i++; continue; }
      if (c === "[") br++; else if (c === "]") br--;
      if (c === ":" && br === 0) {
        const m = sel.slice(i).match(/^:([a-z-]+)\(/);
        if (m && (OPS.includes(m[1]))) {
          const end = closeParen(sel, i + m[0].length);
          if (end < 0) return undefined;
          const arg = sel.slice(i + m[0].length, end);
          const name = m[1];
          // «:not(...)» и «:has(...)» — процедурные, только если внутри есть процедурные операторы
          if ((name === "not" || name === "has") && !isProc(arg)) { buf += sel.slice(i, end + 1); i = end + 1; continue; }
          if (buf.trim()) steps.push({ css: buf.trim() }); else if (buf && steps.length) steps.push({ css: buf });
          buf = "";
          steps.push({ op: name === "-abp-contains" || name === "contains" ? "has-text" : name === "-abp-has" ? "has" : name, arg });
          i = end + 1;
          continue;
        }
      }
      buf += c; i++;
    }
    if (buf.trim()) steps.push({ css: buf.trim() });
    return steps;
  }

  // ---- работа с DOM (подменяется в тестах)
  const Dom = {
    doc: typeof document !== "undefined" ? document : null,
    qsa(root, sel) { try { return Array.from(root.querySelectorAll(sel)); } catch (e) { return []; } },
    matches(el, sel) { try { return el.matches(sel); } catch (e) { return false; } },
    text(el) { return el.textContent || ""; },
    parent(el) { return el.parentElement; },
    closest(el, sel) { try { return el ? el.closest(sel) : null; } catch (e) { return null; } },
    css(el, prop, pseudo) { try { return getComputedStyle(el, pseudo || null).getPropertyValue(prop); } catch (e) { return ""; } },
    attrs(el) { try { return el.getAttributeNames().map((n) => [n, el.getAttribute(n)]); } catch (e) { return []; } },
    xpath(expr, ctx) {
      try {
        const r = document.evaluate(expr, ctx || document, null, XPathResult.UNORDERED_NODE_SNAPSHOT_TYPE, null), out = [];
        for (let k = 0; k < r.snapshotLength; k++) { const n = r.snapshotItem(k); if (n && n.nodeType === 1) out.push(n); }
        return out;
      } catch (e) { return []; }
    },
    media(q) { try { return matchMedia(q).matches; } catch (e) { return false; } },
    path() { return location.pathname + location.search + location.hash; }
  };
  const textPred = (arg) => {
    const m = arg.match(/^\/(.+)\/([gimsu]*)$/);
    if (m) { try { const re = new RegExp(m[1], m[2].replace("g", "")); return (t) => re.test(t); } catch (e) {} }
    const s = arg.replace(/^(["'])(.*)\1$/, "$2");
    return (t) => t.includes(s);
  };
  const valPred = (v) => {                           // «/регэксп/», «*маска*» или точное значение
    const m = v.match(/^\/(.+)\/([gimsu]*)$/);
    if (m) { try { const re = new RegExp(m[1], m[2].replace("g", "")); return (t) => re.test(t); } catch (e) {} }
    const s = v.replace(/^(["'])(.*)\1$/, "$2");
    if (s.includes("*")) { const re = new RegExp("^" + s.split("*").map((x) => x.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$"); return (t) => re.test(t); }
    return (t) => t === s;
  };

  // ---- вычисление шагов; start === null — начинаем со всего документа; scoped — внутри элементов start
  function evalSteps(steps, start, D, mode) {
    D = D || Dom;
    let cur = start, action = null, first = true;
    const all = () => D.qsa(D.doc, "*");
    for (const st of steps) {
      if (st.css !== undefined) {
        const sel = st.css;
        let next = [];
        if (cur === null) next = D.qsa(D.doc, sel);
        else if (mode === "self" && first) next = cur.filter((el) => D.matches(el, sel));         // :not(...) — к самому элементу
        else for (const el of cur) next.push(...D.qsa(el, ":scope " + (/^[>+~]/.test(sel) ? sel : " " + sel)));
        cur = next;
      } else {
        if (cur === null) cur = all();
        const a = st.arg;
        switch (st.op) {
          case "has-text": { const p = textPred(a); cur = cur.filter((el) => p(D.text(el))); break; }
          case "min-text-length": { const n = parseInt(a, 10) || 0; cur = cur.filter((el) => D.text(el).length >= n); break; }
          case "matches-css": case "matches-css-before": case "matches-css-after": {
            const k = a.indexOf(":"); const prop = a.slice(0, k).trim(), p = valPred(a.slice(k + 1).trim());
            const pseudo = st.op === "matches-css-before" ? "::before" : st.op === "matches-css-after" ? "::after" : null;
            cur = cur.filter((el) => p(D.css(el, prop, pseudo)));
            break;
          }
          case "matches-attr": {
            const m = a.match(/^\s*("[^"]*"|'[^']*'|[^=]*?)\s*(?:=\s*("[^"]*"|'[^']*'|.*))?$/);
            const np = valPred(m[1]), vp = m[2] !== undefined ? valPred(m[2]) : null;
            cur = cur.filter((el) => D.attrs(el).some(([n, v]) => np(n) && (!vp || vp(v))));
            break;
          }
          case "matches-media": if (!D.media(a)) cur = []; break;
          case "matches-path": if (!textPred(a)(D.path())) cur = []; break;
          case "not": {
            const sub = parse(a);
            cur = cur.filter((el) => sub ? evalSteps(sub, [el], D, "self").length === 0 : !D.matches(el, a));
            break;
          }
          case "has": {
            const sub = parse(a);
            cur = cur.filter((el) => sub ? evalSteps(sub, [el], D, "scoped").length > 0 : D.qsa(el, ":scope " + a).length > 0);
            break;
          }
          case "upward": case "nth-ancestor": {
            const n = /^\d+$/.test(a.trim()) ? parseInt(a, 10) : 0, out = [];
            for (const el of cur) {
              let x = el;
              if (n) { for (let k = 0; k < n && x; k++) x = D.parent(x); } else x = D.closest(D.parent(el), a);
              if (x && !out.includes(x)) out.push(x);
            }
            cur = out;
            break;
          }
          case "xpath": { const out = []; for (const el of cur) for (const r of D.xpath(a, el === D.doc ? undefined : el)) if (!out.includes(r)) out.push(r); cur = out; break; }
          case "watch-attr": break;
          case "remove": action = { remove: true }; break;
          case "style": action = { style: a }; break;
          default: throw new Error("op");
        }
      }
      first = false;
    }
    cur = cur || [];
    evalSteps.lastAction = action;
    return cur;
  }
  function apply(els, action) {
    for (const el of els) {
      try {
        if (action && action.remove) el.remove();
        else if (action && action.style) for (const d of action.style.split(";")) { const k = d.indexOf(":"); if (k > 0) el.style.setProperty(d.slice(0, k).trim(), d.slice(k + 1).replace(/!important/i, "").trim(), "important"); }
        else el.style.setProperty("display", "none", "important");
      } catch (e) {}
    }
  }

  // ---- запуск на странице: правила применяются при загрузке и при изменениях страницы
  const rules = [], known = new Set();
  let timer = null;
  function run() {
    timer = null;
    for (const r of rules) {
      try {
        const els = evalSteps(r.steps, null, Dom);
        apply(els, evalSteps.lastAction);
      } catch (e) {}
    }
  }
  function schedule() { if (!timer) timer = setTimeout(run, 400); }
  function add(list) {
    for (const s of list || []) {
      if (known.has(s) || rules.length > 1500) continue;
      known.add(s);
      let steps; try { steps = parse(s); } catch (e) { steps = null; }
      if (steps && steps.length) rules.push({ steps });
    }
    schedule();
  }
  if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    const start = () => { if (document.documentElement) new MutationObserver(() => { if (rules.length) schedule(); }).observe(document.documentElement, { childList: true, subtree: true }); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  }
  window.__babeloProc = { parse, evalSteps, add, apply, Dom };
})();
