/* StruggleTime UI component library (vanilla) */
(function (global) {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "className") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of [].concat(children)) {
      if (child == null || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  /* ---- Toaster ---- */
  let toastRoot;
  function ensureToastRoot() {
    if (toastRoot) return toastRoot;
    toastRoot = el("div", { className: "ui-toasts", "aria-live": "polite" });
    document.body.appendChild(toastRoot);
    return toastRoot;
  }
  function toast(message, opts = {}) {
    const root = ensureToastRoot();
    const kind = opts.kind || "info";
    const glyph = opts.glyph || (kind === "ok" ? "checkmark" : kind === "error" ? "important" : "info");
    const node = el("div", { className: `ui-toast ui-toast-${kind}`, role: "status" }, [
      icon(glyph, { size: 16, className: "ui-toast-ico", title: kind }),
      el("div", { className: "ui-toast-msg", text: message }),
      opts.action
        ? el("button", { className: "ui-toast-action", type: "button", onClick: () => { opts.action.onClick?.(); node.remove(); } }, [
            opts.action.glyph ? icon(opts.action.glyph, { size: 14 }) : null,
            opts.action.label,
          ])
        : null,
      el("button", { className: "ui-icon-btn ui-toast-x", type: "button", "aria-label": "dismiss", onClick: () => node.remove() }, [
        icon("view-close", { size: 14, title: "dismiss" }),
      ]),
    ]);
    root.appendChild(node);
    const ms = opts.duration ?? (kind === "error" ? 5500 : 3200);
    if (ms > 0) setTimeout(() => node.remove(), ms);
    return node;
  }

  /* ---- Modal ---- */
  function modal({ title, body, confirmText = "Save", cancelText = "Cancel", glyph = "edit", confirmGlyph = "checkmark", cancelGlyph = "view-close", onConfirm, onCancel }) {
    const backdrop = el("div", { className: "ui-modal-backdrop", role: "dialog", "aria-modal": "true" });
    const card = el("div", { className: "ui-modal" }, [
      el("div", { className: "ui-modal-head" }, [
        el("div", { className: "ui-modal-title" }, [
          icon(glyph, { size: 18, title }),
          el("h3", { text: title }),
        ]),
        el("button", { className: "ui-icon-btn", type: "button", "aria-label": "close", onClick: close }, [
          icon("view-close", { size: 16, title: "close" }),
        ]),
      ]),
      el("div", { className: "ui-modal-body" }, [body]),
      el("div", { className: "ui-modal-foot" }, [
        el("button", { className: "ui-btn ui-btn-ghost", type: "button", onClick: close }, [
          icon(cancelGlyph, { size: 14 }),
          cancelText,
        ]),
        el("button", { className: "ui-btn", type: "button", onClick: async () => {
          try {
            const ok = await onConfirm?.();
            if (ok !== false) close();
          } catch (e) { toast(String(e.message || e), { kind: "error" }); }
        } }, [
          icon(confirmGlyph, { size: 14 }),
          confirmText,
        ]),
      ]),
    ]);
    function close() {
      backdrop.remove();
      onCancel?.();
    }
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    const focus = card.querySelector("input,select,textarea,button.ui-btn:not(.ui-btn-ghost)");
    focus?.focus();
    return { close, el: backdrop };
  }

  function field({ label, id, type = "text", value = "", placeholder = "", required = false }) {
    return el("label", { className: "ui-field" }, [
      el("span", { text: label }),
      el("input", { id, type, value, placeholder, required: required || undefined, className: "ui-input" }),
    ]);
  }

  function btn(text, opts = {}) {
    const kids = [];
    if (opts.glyph) kids.push(icon(opts.glyph, { size: 14 }));
    kids.push(text);
    return el("button", {
      type: opts.type || "button",
      className: `ui-btn ${opts.ghost ? "ui-btn-ghost" : ""} ${opts.className || ""}`.trim(),
      disabled: opts.disabled || undefined,
      onClick: opts.onClick,
    }, kids);
  }

  function pill(ok, yes, no) {
    return el("span", { className: `ui-pill ${ok ? "ok" : "bad"}`, text: ok ? yes : no });
  }

  function empty(text) {
    return el("p", { className: "ui-empty", text });
  }

  function tabs(items, { value, onChange }) {
    const root = el("div", { className: "ui-tabs", role: "tablist" });
    function paint(active) {
      root.innerHTML = "";
      items.forEach((it) => {
        root.appendChild(el("button", {
          type: "button",
          role: "tab",
          className: `ui-tab ${it.value === active ? "on" : ""}`,
          text: it.label,
          onClick: () => { paint(it.value); onChange?.(it.value); },
        }));
      });
    }
    paint(value || items[0]?.value);
    return root;
  }

  function avatar(url, handle) {
    if (url) return el("img", { className: "ui-avatar", src: url, alt: handle || "" });
    return el("div", { className: "ui-avatar ui-avatar-fallback", text: (handle || "?").slice(0, 1).toUpperCase() });
  }

  function panel(children, className = "") {
    return el("div", { className: `ui-panel ${className}`.trim() }, children);
  }

  function searchInput({ placeholder = "Search…", onInput, glyph = "search" }) {
    return el("div", { className: "ui-search" }, [
      icon(glyph, { size: 14, title: "search" }),
      el("input", { className: "ui-input", type: "search", placeholder, onInput: (e) => onInput?.(e.target.value) }),
    ]);
  }

  /* Hack Club Icons CDN — silhouette used as CSS mask with currentColor (theme tokens) */
  function icon(glyph, { size = 18, className = "", title } = {}) {
    const url = `https://icons.hackclub.com/api/icons/black/${encodeURIComponent(glyph)}`;
    return el("span", {
      className: `hc-icon ${className}`.trim(),
      role: "img",
      "aria-label": title || glyph,
      title: title || glyph,
      style: `width:${size}px;height:${size}px;--hc-url:url(${url})`,
    });
  }

  function iconBtn({ glyph, title, onClick, ghost = true, className = "" }) {
    return el("button", {
      type: "button",
      className: `ui-icon-action ${ghost ? "ghost" : ""} ${className}`.trim(),
      title: title || glyph,
      "aria-label": title || glyph,
      onClick,
    }, [icon(glyph, { size: 16, title })]);
  }

  function selectField({ label, id, options = [], value = "", onChange, glyph }) {
    const sel = el("select", {
      id, className: "ui-input",
      onChange: (e) => onChange?.(e.target.value),
    }, (options || []).filter((o) => o && o.id != null).map((o) => {
      const opt = el("option", { value: String(o.id), text: o.label != null ? String(o.label) : String(o.id) });
      if (String(o.id) === String(value)) opt.selected = true;
      return opt;
    }));
    const allowed = (options || []).filter((o) => o && o.id != null).map((o) => String(o.id));
    sel.value = allowed.includes(String(value)) ? String(value) : (allowed[0] || "");
    return el("label", { className: "ui-filter" }, [
      el("span", { className: "ui-filter-label" }, [
        glyph ? icon(glyph, { size: 14, className: "ui-filter-ico" }) : null,
        el("span", { text: label }),
      ]),
      sel,
    ]);
  }

  /** Searchable dropdown: filter box + select. options: [{id, label, search?}] */
  function comboField({ label, id, options = [], value = "", glyph = "search", placeholder = "filter…", onChange, hint }) {
    const state = { options: options.slice(), value: value == null ? "" : String(value) };
    const sel = el("select", {
      id, className: "ui-input",
      onChange: (e) => { state.value = e.target.value; onChange?.(e.target.value); },
    });
    function paint(filter = "") {
      const q = filter.trim().toLowerCase();
      const filtered = state.options.filter((o) => {
        if (!q) return true;
        const hay = `${o.label || ""} ${o.search || ""} ${o.id || ""}`.toLowerCase();
        return hay.includes(q);
      });
      const keep = filtered.length ? filtered : state.options.slice(0, 1);
      sel.innerHTML = "";
      keep.forEach((o) => {
        const opt = el("option", { value: o.id == null ? "" : String(o.id), text: o.label });
        sel.appendChild(opt);
      });
      const ids = keep.map((o) => String(o.id == null ? "" : o.id));
      if (ids.includes(state.value)) sel.value = state.value;
      else if (keep.length) {
        sel.value = String(keep[0].id == null ? "" : keep[0].id);
        state.value = sel.value;
      }
    }
    paint();
    const search = el("input", {
      className: "ui-input ui-combo-search",
      type: "search",
      placeholder,
      onInput: (e) => paint(e.target.value),
    });
    const wrap = el("label", { className: "ui-field ui-combo" }, [
      el("span", { className: "ui-field-label" }, [
        glyph ? icon(glyph, { size: 14, className: "ui-field-ico" }) : null,
        el("span", { text: label }),
      ]),
      el("div", { className: "ui-combo-row" }, [
        icon("search", { size: 14, className: "ui-combo-search-ico" }),
        search,
      ]),
      sel,
      hint ? el("span", { className: "ui-field-hint", text: hint }) : null,
    ]);
    wrap.getValue = () => sel.value;
    wrap.setOptions = (next, nextValue) => {
      state.options = (next || []).slice();
      if (nextValue !== undefined) state.value = nextValue == null ? "" : String(nextValue);
      paint(search.value || "");
      onChange?.(sel.value);
    };
    wrap.setValue = (v) => {
      state.value = v == null ? "" : String(v);
      paint(search.value || "");
    };
    return wrap;
  }

  /** Local directory path + choose button (browser path input / directory picker when available). */
  function pathField({ label, id, value = "", glyph = "docs", placeholder = "/path/to/project", onChange }) {
    const input = el("input", {
      id, className: "ui-input", type: "text", value: value || "", placeholder,
      onInput: (e) => onChange?.(e.target.value),
    });
    async function pick() {
      if (typeof window.showDirectoryPicker === "function") {
        try {
          const handle = await window.showDirectoryPicker({ mode: "read" });
          // Browsers rarely expose absolute paths; prefer typed path, use folder name as hint.
          if (!input.value.trim() && handle?.name) {
            input.value = handle.name;
            onChange?.(input.value);
            toast("Browsers hide full paths — paste the absolute folder path for the tray daemon.", { kind: "info", duration: 4500 });
          } else {
            toast("Paste the absolute folder path the tray should track.", { kind: "info" });
          }
          input.focus();
          return;
        } catch (e) {
          if (e && e.name === "AbortError") return;
        }
      }
      input.focus();
      toast("Enter the absolute local folder path for the tray.", { kind: "info" });
    }
    const wrap = el("label", { className: "ui-field" }, [
      el("span", { className: "ui-field-label" }, [
        glyph ? icon(glyph, { size: 14, className: "ui-field-ico" }) : null,
        el("span", { text: label }),
      ]),
      el("div", { className: "ui-path-row" }, [
        input,
        el("button", {
          type: "button",
          className: "ui-btn ui-btn-ghost ui-path-btn",
          title: "Choose directory",
          onClick: pick,
        }, [icon("docs", { size: 14 }), "Choose"]),
      ]),
      el("span", { className: "ui-field-hint", text: "Optional on the web; the tray uses this path for --repo." }),
    ]);
    wrap.getValue = () => input.value.trim();
    wrap.setValue = (v) => { input.value = v || ""; };
    return wrap;
  }

  /** Theme-token SVG pie — slices use CSS vars, no hardcoded palette */
  function pieChart(items = [], { size = 152, hole = 0.52 } = {}) {
    const wrap = el("div", { className: "ui-pie" });
    const total = items.reduce((s, it) => s + (Number(it.hours) || Number(it.value) || 0), 0);
    if (!items.length || total <= 0) {
      wrap.appendChild(el("p", { className: "ui-empty compact", text: "No share to chart yet." }));
      return wrap;
    }
    const vars = ["var(--accent)", "var(--accent2)", "var(--bar)", "var(--mut)", "var(--dump)"];
    const tip = el("div", { className: "ui-pie-tip", text: "hover a slice" });
    const r = size / 2;
    const ir = hole > 0 ? r * hole : 0;
    let angle = -Math.PI / 2;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("class", "ui-pie-svg");
    items.forEach((it, i) => {
      const v = Number(it.hours) || Number(it.value) || 0;
      if (v <= 0) return;
      const sweep = (v / total) * Math.PI * 2;
      const a0 = angle;
      const a1 = angle + Math.max(sweep, 0.001);
      angle += sweep;
      const x0 = r + r * Math.cos(a0), y0 = r + r * Math.sin(a0);
      const x1 = r + r * Math.cos(a1), y1 = r + r * Math.sin(a1);
      const large = sweep > Math.PI ? 1 : 0;
      let d;
      if (ir > 0) {
        const xi0 = r + ir * Math.cos(a0), yi0 = r + ir * Math.sin(a0);
        const xi1 = r + ir * Math.cos(a1), yi1 = r + ir * Math.sin(a1);
        d = `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${ir} ${ir} 0 ${large} 0 ${xi0} ${yi0} Z`;
      } else {
        d = `M ${r} ${r} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
      }
      const pct = Math.round((v / total) * 100);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", vars[i % vars.length]);
      path.setAttribute("class", "ui-pie-slice");
      path.addEventListener("mouseenter", () => { tip.textContent = `${it.label} · ${pct}%`; });
      path.addEventListener("mouseleave", () => { tip.textContent = "hover a slice"; });
      svg.appendChild(path);
    });
    const legend = el("div", { className: "ui-pie-legend" }, items.filter((it) => (Number(it.hours) || Number(it.value) || 0) > 0).map((it, i) => {
      const pct = Math.round(((Number(it.hours) || Number(it.value) || 0) / total) * 100);
      return el("div", { className: "ui-pie-leg" }, [
        el("i", { style: `background:${vars[i % vars.length]}` }),
        el("span", { text: String(it.label) }),
        el("b", { text: `${pct}%` }),
      ]);
    }));
    wrap.append(svg, el("div", { className: "ui-pie-side" }, [tip, legend]));
    return wrap;
  }

  global.UI = { esc, el, toast, modal, field, btn, pill, empty, tabs, avatar, panel, searchInput, icon, iconBtn, selectField, comboField, pathField, pieChart };
})(window);
