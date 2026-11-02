// SiteGenius Studio — utilidades compartilhadas (ícones SVG, elementos, datas). Sem innerHTML.
(() => {
  "use strict";

  // Ícones de traço (24x24, stroke 2), desenhados com createElementNS (permitido pela CSP).
  const PATHS = {
    plus: ["M12 5v14", "M5 12h14"],
    grid: ["M3 3h7v7H3z", "M14 3h7v7h-7z", "M14 14h7v7h-7z", "M3 14h7v7H3z"],
    download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
    refresh: ["M23 4v6h-6", "M1 20v-6h6", "M3.51 9a9 9 0 0 1 14.85-3.36L23 10", "M1 14l4.64 4.36A9 9 0 0 0 20.49 15"],
    edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
    x: ["M18 6L6 18", "M6 6l12 12"],
    logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
    key: ["M21 2l-2 2", "M15.5 7.5l3 3L22 7l-3-3", "M11.39 11.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78z", "M11.4 11.6L19 4"],
    shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"],
    chevron: ["M6 9l6 6 6-6"],
    send: ["M12 19V5", "M5 12l7-7 7 7"],
    clip: ["M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"],
    open: ["M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6", "M15 3h6v6", "M10 14L21 3"],
    copy: ["M9 9h11v11H9z", "M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"],
    check: ["M20 6L9 17l-5-5"],
    ban: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M4.93 4.93l14.14 14.14"],
    users: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
    image: ["M3 3h18v18H3z", "M8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z", "M21 15l-5-5L5 21"],
    sparkle: ["M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z", "M19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8z"],
    menu: ["M3 12h18", "M3 6h18", "M3 18h18"],
    arrowLeft: ["M19 12H5", "M12 19l-7-7 7-7"],
  };

  function icon(name, size = 18) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
      "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon" })) {
      svg.setAttribute(k, v);
    }
    for (const d of PATHS[name] || []) {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      svg.append(path);
    }
    return svg;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children) if (child != null && child !== false) node.append(child);
    return node;
  }

  // Botão com ícone + texto (o texto continua visível: nunca só ícone, exceto com aria-label).
  function button(label, iconName, attrs = {}) {
    return el(attrs.href ? "a" : "button", { type: attrs.href ? null : "button", ...attrs },
      iconName ? icon(iconName, attrs.iconSize || 16) : null, label ? el("span", { text: label }) : null);
  }

  function fmtSeconds(s) {
    if (s == null) return "";
    s = Math.round(s);
    return s < 90 ? `${s}s` : `${Math.floor(s / 60)}min ${String(s % 60).padStart(2, "0")}s`;
  }

  function fmtDate(ts, withTime = true) {
    if (!ts) return "—";
    const opts = withTime ? { dateStyle: "short", timeStyle: "short" } : { day: "2-digit", month: "2-digit" };
    return new Date(ts * 1000).toLocaleString("pt-BR", opts);
  }

  function initials(name) {
    return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
  }

  window.SG = { icon, el, button, fmtSeconds, fmtDate, initials };
})();
