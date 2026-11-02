// SiteGenius Studio — painel de administração (contas, limites e uso). Sem innerHTML.
(() => {
  "use strict";

  const { el, icon, button, fmtSeconds, fmtDate, initials } = window.SG;
  const $ = (sel) => document.querySelector(sel);
  const csrf = document.querySelector('meta[name="sg-csrf"]').content;
  const state = { users: [], defaultDaily: 20, editing: null };

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", headers: {}, ...options };
    if (opts.method && opts.method !== "GET") opts.headers["X-SG-CSRF"] = csrf;
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const resp = await fetch("/studio/api" + path, opts);
    let data = {};
    try { data = await resp.json(); } catch (_) { /* sem JSON */ }
    if (resp.status === 401) { location.assign("/studio/entrar"); throw new Error("Sessão encerrada."); }
    if (!resp.ok || data.ok === false) throw new Error(data.erro || `Erro ${resp.status}`);
    return data;
  }

  let toastTimer;
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 4000);
  }

  // ------------------------------------------------------------ indicadores

  function kpi(label, value, sub) {
    return el("div", { class: "kpi" },
      el("span", { class: "kpi-label", text: label }),
      el("b", { class: "kpi-value", text: value }),
      sub ? el("span", { class: "kpi-sub", text: sub }) : null);
  }

  function renderStats(st) {
    $("#kpis").replaceChildren(
      kpi("Usuários ativos", String(st.usuarios_ativos), `de ${st.usuarios_total} cadastrados`),
      kpi("Imagens hoje", String(st.imagens_hoje)),
      kpi("Imagens no mês", String(st.imagens_mes)),
      kpi("Imagens no total", String(st.imagens_total)),
      kpi("Tempo médio de geração", st.media_geracao_s ? fmtSeconds(st.media_geracao_s) : "—", "no mês, sem a fila"),
      kpi("Fila agora", String(st.fila), st.gerador_online ? "gerador online" : "gerador offline"));
    const gpu = $("#pill-gpu");
    gpu.textContent = st.gerador_online ? "Gerador online" : "Gerador offline";
    gpu.className = "pill " + (st.gerador_online ? "on" : "off");
    const ia = $("#pill-ia");
    ia.textContent = st.interpretacao === "ia" ? "IA HostGator" : "Modo regras";
    ia.className = "pill " + (st.interpretacao === "ia" ? "on" : "off");
    renderChart(st.ultimos_14_dias);
  }

  // Barras de série única: cor primária, topo arredondado, 2px de intervalo, dica ao passar o mouse.
  function renderChart(days) {
    const ns = "http://www.w3.org/2000/svg";
    // Desenha na largura real do contêiner: texto e barras ficam no tamanho certo (sem escalar o SVG).
    const W = Math.max(320, Math.round($("#chart").clientWidth || 700)), H = 200, padL = 32, padB = 24, padT = 12;
    const max = Math.max(4, ...days.map((d) => d.imagens));
    const niceMax = Math.ceil(max / 4) * 4;
    const slot = (W - padL) / days.length;
    const bw = Math.min(28, slot - 2);
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "bars");
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    const mk = (tag, attrs) => {
      const n = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      return n;
    };
    for (let i = 0; i <= 4; i++) {
      const y = padT + (H - padT - padB) * (1 - i / 4);
      svg.append(mk("line", { x1: padL, x2: W, y1: y, y2: y, class: i ? "grid" : "axis" }));
      const t = mk("text", { x: padL - 6, y: y + 4, class: "tick", "text-anchor": "end" });
      t.textContent = String((niceMax * i) / 4);
      svg.append(t);
    }
    const tip = $("#chart-tip");
    days.forEach((d, i) => {
      const h = ((H - padT - padB) * d.imagens) / niceMax;
      const x = padL + i * slot + (slot - bw) / 2;
      const y = H - padB - h;
      if (d.imagens > 0) {
        const r = Math.min(4, h / 2, bw / 2);
        // Retângulo com só os cantos de cima arredondados, ancorado na linha de base.
        svg.append(mk("path", {
          class: "bar",
          d: `M${x},${H - padB} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${H - padB} Z`,
        }));
      }
      if (i % 2 === 0 || i === days.length - 1) {
        const t = mk("text", { x: x + bw / 2, y: H - 6, class: "tick", "text-anchor": "middle" });
        t.textContent = fmtDate(d.dia + 12 * 3600, false);
        svg.append(t);
      }
      // Área de toque maior que a barra (coluna inteira).
      const hit = mk("rect", { x: padL + i * slot, y: padT, width: slot, height: H - padT - padB, class: "hit" });
      hit.addEventListener("mouseenter", () => {
        tip.hidden = false;
        tip.replaceChildren(el("b", { text: `${d.imagens} imagem${d.imagens === 1 ? "" : "ns"}` }),
          el("span", { text: fmtDate(d.dia + 12 * 3600, false) }));
        const box = $("#chart").getBoundingClientRect();
        tip.style.left = `${((padL + i * slot + slot / 2) / W) * box.width}px`;
        tip.style.top = `${(y / H) * box.height}px`;
      });
      hit.addEventListener("mouseleave", () => { tip.hidden = true; });
      svg.append(hit);
    });
    $("#chart").replaceChildren(svg);
    $("#chart-rows").replaceChildren(...days.slice().reverse().map((d) =>
      el("tr", {}, el("td", { text: new Date((d.dia + 12 * 3600) * 1000).toLocaleDateString("pt-BR") }),
        el("td", { class: "num", text: String(d.imagens) }))));
  }

  // ------------------------------------------------------------ usuários

  function meterCell(used, limit) {
    const cell = el("td", { class: "usage-cell" });
    cell.append(el("span", { class: "usage-num", text: limit == null ? `${used}` : `${used} / ${limit}` }));
    if (limit == null) {
      cell.append(el("small", { class: "muted", text: "sem limite" }));
      return cell;
    }
    const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 100;
    const level = pct >= 100 ? "full" : pct >= 80 ? "high" : "";
    const fill = el("span");
    fill.style.width = `${pct}%`;
    cell.append(el("div", { class: `meter ${level}`, role: "meter", "aria-valuenow": used, "aria-valuemin": 0, "aria-valuemax": limit }, fill));
    if (level) cell.append(el("small", { class: `usage-note ${level}`, text: level === "full" ? "Limite atingido" : "Perto do limite" }));
    return cell;
  }

  function badge(text, kind) {
    return el("span", { class: `tag ${kind}`, text });
  }

  function userRow(u) {
    const who = el("td", { class: "user-cell" },
      el("span", { class: `avatar ${u.ativo ? "" : "off"}`, text: initials(u.nome) }),
      el("div", {},
        el("div", { class: "user-name" }, el("b", { text: u.nome }),
          u.admin ? badge("Admin", "admin") : null,
          !u.ativo ? badge("Inativo", "off") : null,
          u.trocar_senha ? badge("Senha provisória", "warn") : null),
        el("div", { class: "muted small", text: u.email }),
        u.observacoes ? el("div", { class: "muted small", text: u.observacoes }) : null));
    const actions = el("td", { class: "actions-col" }, el("div", { class: "row-actions" },
      button("Editar", "edit", { class: "btn btn-small btn-ghost", onclick: () => openEdit(u) }),
      button("Senha", "key", { class: "btn btn-small btn-ghost", title: "Gerar nova senha provisória", onclick: () => resetPassword(u) }),
      u.ativo
        ? button("Desativar", "ban", { class: "btn btn-small btn-danger-ghost", onclick: () => setActive(u, false) })
        : button("Ativar", "check", { class: "btn btn-small btn-ghost", onclick: () => setActive(u, true) })));
    return el("tr", { class: u.ativo ? "" : "row-off" },
      who,
      meterCell(u.imagens_hoje, u.limite_diario_efetivo),
      meterCell(u.imagens_mes, u.limite_mensal),
      el("td", { class: "num", text: String(u.imagens_total) }),
      el("td", { class: "small", text: fmtDate(u.ultimo_login) }),
      actions);
  }

  function renderUsers() {
    const q = $("#user-search").value.trim().toLowerCase();
    const list = state.users.filter((u) => !q || u.nome.toLowerCase().includes(q) || u.email.includes(q));
    $("#users-rows").replaceChildren(...list.map(userRow));
    if (!list.length) {
      $("#users-rows").append(el("tr", {}, el("td", { colspan: 6, class: "muted empty-row", text: q ? "Nenhum usuário encontrado." : "Nenhum usuário ainda." })));
    }
    const active = state.users.filter((u) => u.ativo).length;
    $("#users-sub").textContent = `${active} ativo${active === 1 ? "" : "s"} de ${state.users.length} · limite diário padrão: ${state.defaultDaily} imagens`;
  }

  function renderJobs(jobs) {
    const fmt = (v) => (v == null ? "—" : fmtSeconds(v));
    $("#jobs-rows").replaceChildren(...jobs.map((j) => {
      const t = j.tempos || {};
      const stages = [["texto", t.encode_s], ["preparo", t.preparo_amostragem_s], ["amostragem", t.sampling_s], ["VAE", t.vae_s]]
        .filter(([, v]) => v != null).map(([n, v]) => `${n} ${fmtSeconds(v)}`);
      if (t.steps) stages.push(`${t.steps} passos`);
      if (t.vram_min_sampling_mb != null) stages.push(`VRAM mín. ${Math.round(t.vram_min_sampling_mb)} MB`);
      const size = j.tamanho ? `${j.tamanho[0]}×${j.tamanho[1]}` : "";
      return el("tr", {},
        el("td", { class: "small", text: fmtDate(j.criado_em) }),
        el("td", { text: j.usuario }),
        el("td", { class: "small", text: [j.tipo, size, j.modo].filter(Boolean).join(" · ") }),
        el("td", {}, badge(j.status, j.status === "failed" ? "off" : j.status === "done" ? "admin" : "warn")),
        el("td", { class: "num small", text: fmt(j.fila_s) }),
        el("td", { class: "num small", text: fmt(j.geracao_s) }),
        el("td", { class: "small", text: stages.join(" · ") }),
        el("td", { class: "small muted", text: [j.detalhe, j.worker].filter(Boolean).join(" · ") }));
    }));
    if (!jobs.length) $("#jobs-rows").append(el("tr", {}, el("td", { colspan: 8, class: "muted empty-row", text: "Nenhuma atividade ainda." })));
  }

  async function load() {
    api("/admin/jobs").then((d) => renderJobs(d.jobs)).catch(() => {});
    const [stats, users] = await Promise.all([api("/admin/stats"), api("/admin/users")]);
    renderStats(stats);
    state.users = users.usuarios;
    state.defaultDaily = users.limite_diario_padrao;
    $("#f-daily").placeholder = `padrão (${state.defaultDaily})`;
    renderUsers();
  }

  // ------------------------------------------------------------ criar / editar

  function openCreate() {
    state.editing = null;
    const form = $("#user-form");
    form.reset();
    $("#user-dialog-title").textContent = "Novo usuário";
    $("#user-submit").textContent = "Criar usuário";
    $("#email-field").hidden = false;
    form.elements.email.required = true;
    $("#user-error").textContent = "";
    $("#user-dialog").showModal();
  }

  function openEdit(u) {
    state.editing = u;
    const form = $("#user-form");
    form.reset();
    $("#user-dialog-title").textContent = `Editar ${u.nome}`;
    $("#user-submit").textContent = "Salvar";
    $("#email-field").hidden = true;  // o e-mail é o login: não muda aqui
    form.elements.email.required = false;
    form.elements.nome.value = u.nome;
    form.elements.limite_diario.value = u.limite_diario ?? "";
    form.elements.limite_mensal.value = u.limite_mensal ?? "";
    form.elements.observacoes.value = u.observacoes;
    form.elements.admin.checked = u.admin;
    $("#user-error").textContent = "";
    $("#user-dialog").showModal();
  }

  async function submitUser(event) {
    event.preventDefault();
    const f = event.target.elements;
    const body = {
      nome: f.nome.value, limite_diario: f.limite_diario.value === "" ? null : Number(f.limite_diario.value),
      limite_mensal: f.limite_mensal.value === "" ? null : Number(f.limite_mensal.value),
      observacoes: f.observacoes.value, admin: f.admin.checked,
    };
    try {
      if (state.editing) {
        await api(`/admin/users/${state.editing.id}`, { method: "PATCH", json: body });
        $("#user-dialog").close();
        toast("Usuário atualizado.");
      } else {
        const out = await api("/admin/users", { method: "POST", json: { ...body, email: f.email.value } });
        $("#user-dialog").close();
        showSecret("Usuário criado", `Conta de ${body.nome} (${f.email.value}). Senha provisória:`, out.senha_provisoria);
      }
      await load();
    } catch (err) {
      $("#user-error").textContent = err.message;
    }
  }

  async function setActive(u, active) {
    if (!active && !confirm(`Desativar ${u.nome}? A pessoa perde o acesso na hora (as imagens ficam guardadas).`)) return;
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", json: { ativo: active } });
      toast(active ? "Usuário ativado." : "Usuário desativado.");
      await load();
    } catch (err) {
      toast(err.message);
    }
  }

  async function resetPassword(u) {
    if (!confirm(`Gerar uma nova senha provisória para ${u.nome}? A senha atual deixa de funcionar e as sessões abertas são encerradas.`)) return;
    try {
      const out = await api(`/admin/users/${u.id}/reset-password`, { method: "POST", json: {} });
      showSecret("Nova senha provisória", `Para ${u.nome} (${u.email}):`, out.senha_provisoria);
      await load();
    } catch (err) {
      toast(err.message);
    }
  }

  function showSecret(title, text, secret) {
    $("#secret-title").textContent = title;
    $("#secret-text").textContent = text;
    $("#secret-value").textContent = secret;
    $("#secret-dialog").showModal();
  }

  // ------------------------------------------------------------ início

  function bind() {
    $("#back-link").append(icon("arrowLeft", 16), el("span", { text: "Voltar ao Studio" }));
    $("#btn-new-user").append(icon("plus", 18), el("span", { text: "Novo usuário" }));
    $("#secret-copy").append(icon("copy", 16), el("span", { text: "Copiar" }));
    $("#btn-new-user").addEventListener("click", openCreate);
    $("#user-form").addEventListener("submit", submitUser);
    $("#user-search").addEventListener("input", renderUsers);
    $("#secret-copy").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("#secret-value").textContent);
        toast("Senha copiada.");
      } catch (_) {
        toast("Não foi possível copiar: selecione e copie manualmente.");
      }
    });
    document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  }

  bind();
  load().catch((err) => toast(err.message));
  setInterval(() => api("/admin/stats").then(renderStats).catch(() => {}), 30000);
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => api("/admin/stats").then(renderStats).catch(() => {}), 300);
  });
})();
