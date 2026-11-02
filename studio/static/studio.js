// SiteGenius Studio — chat de criação de imagens (livre) e de encartes.
// Todo texto vindo do usuário/servidor é inserido com textContent (nunca innerHTML).
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  let csrf = document.querySelector('meta[name="sg-csrf"]').content;
  const { icon, button } = window.SG;
  const ACTIVE = ["queued", "claimed", "running"];
  const state = {
    conversationId: null,
    mode: "free",
    briefing: null,
    pendingAttachments: [],
    pollTimer: null,
    saveTimer: null,
    photoTarget: null,
    formats: {},
    quality: "qualidade",
    estimates: {},
    estimatesAt: 0,
    profiles: null,
    jobs: {},
  };

  // ------------------------------------------------------------ util

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", headers: {}, ...options };
    if (opts.method && opts.method !== "GET") opts.headers["X-SG-CSRF"] = csrf;
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    let resp;
    try {
      resp = await fetch("/studio/api" + path, opts);
    } catch (_) {
      throw new Error("Sem conexão no momento. Verifique sua internet e tente de novo.");
    }
    let data = {};
    try { data = await resp.json(); } catch (_) { /* resposta sem JSON */ }
    if (resp.status === 401) { location.assign("/studio/entrar"); throw new Error("Sua sessão terminou. Entre novamente."); }
    if (!resp.ok || data.ok === false) {
      throw new Error(data.erro || (resp.status >= 500 ? "Algo deu errado do nosso lado. Tente novamente em instantes."
        : "Não foi possível concluir. Tente novamente."));
    }
    return data;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children) if (child) node.append(child);
    return node;
  }

  let toastTimer;
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 4500);
  }

  const assetUrl = (id) => `/studio/api/assets/${encodeURIComponent(id)}`;

  function fmtSeconds(s) {
    if (s == null) return "";
    s = Math.max(1, Math.round(s));
    if (s < 60) return `${s} s`;
    const m = Math.floor(s / 60), r = s % 60;
    return r ? `${m} min ${r} s` : `${m} min`;
  }

  // "cerca de 3 minutos" / "menos de 1 minuto": estimativa simples, sem falsa precisão.
  function friendlyDuration(s) {
    if (s == null) return "";
    if (s < 50) return "menos de 1 minuto";
    const m = Math.max(1, Math.round(s / 60));
    return `cerca de ${m} minuto${m > 1 ? "s" : ""}`;
  }

  async function uploadFile(file, kind) {
    const form = new FormData();
    form.append("arquivo", file);
    form.append("tipo", kind);
    const data = await api("/uploads", { method: "POST", body: form });
    return data.asset;
  }

  // ------------------------------------------------------------ status e estimativas (medidas reais)

  async function loadMe() {
    const me = await api("/me");
    state.formats = me.formatos;
    renderUserMenu(me);
    if (me.trocar_senha) openPasswordDialog(true);
    $("#f-formato").replaceChildren(...Object.entries(me.formatos).map(([k, v]) => el("option", { value: k, text: v })));
    const aspect = $("#opt-aspect").value;
    $("#opt-aspect").replaceChildren(el("option", { value: "", text: "Automático" }),
      ...me.proporcoes.map(([k, v]) => el("option", { value: k, text: v })));
    $("#opt-aspect").value = aspect;
    $("#opt-style").replaceChildren(...me.estilos.map(([k, v]) => el("option", { value: k, text: v })));
    state.profiles = me.perfis;
  }

  function usageText(used, limit) {
    return limit == null ? `${used}` : `${used}/${limit}`;
  }

  function renderUserMenu(me) {
    state.me = me;
    $("#user-name").textContent = me.nome || "";
    $("#user-avatar").textContent = window.SG.initials(me.nome);
    $("#menu-name").textContent = me.nome || "";
    $("#menu-email").textContent = me.email || "";
    const u = me.uso;
    $("#user-usage").textContent = `hoje ${usageText(u.hoje, u.limite_diario)}`;
    const meter = (label, used, limit) => {
      const row = el("div", { class: "usage-row" },
        el("div", { class: "usage-label" }, el("span", { text: label }),
          el("b", { text: limit == null ? `${used} (sem limite)` : `${used} de ${limit}` })));
      if (limit != null) {
        const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 100;
        const fill = el("span");
        fill.style.width = `${pct}%`;
        const level = pct >= 100 ? "full" : pct >= 80 ? "high" : "";
        row.append(el("div", { class: `meter ${level}` }, fill));
        if (level) row.append(el("small", { class: `usage-note ${level}`, text: level === "full" ? "Limite atingido" : "Perto do limite" }));
      }
      return row;
    };
    $("#menu-usage").replaceChildren(meter("Imagens hoje", u.hoje, u.limite_diario), meter("Imagens no mês", u.mes, u.limite_mensal),
      el("small", { class: "muted", text: `${u.total} imagens no total` }));
    $("#menu-admin").hidden = !me.admin;
  }

  function toggleUserMenu(force) {
    const menu = $("#user-dropdown");
    const open = force ?? menu.hidden;
    menu.hidden = !open;
    $("#user-btn").setAttribute("aria-expanded", String(open));
    if (open) api("/me").then(renderUserMenu).catch(() => {});
  }

  // ------------------------------------------------------------ troca de senha

  function openPasswordDialog(forced) {
    state.passwordForced = forced;
    const dialog = $("#password-dialog");
    $("#password-form").reset();
    $("#password-error").textContent = "";
    $("#password-title").textContent = forced ? "Crie sua senha" : "Trocar senha";
    $("#password-hint").textContent = forced
      ? "Sua conta foi criada com uma senha provisória. Crie uma senha pessoal (mínimo de 10 caracteres) para continuar."
      : "Use pelo menos 10 caracteres. As sessões em outros aparelhos serão encerradas.";
    $("#password-cancel").hidden = forced;
    toggleUserMenu(false);
    dialog.showModal();
  }

  async function submitPassword(event) {
    event.preventDefault();
    const form = event.target;
    const data = Object.fromEntries(new FormData(form));
    if (data.nova !== data.repete) { $("#password-error").textContent = "As senhas novas não conferem."; return; }
    try {
      const resp = await fetch("/studio/auth/trocar-senha", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-SG-CSRF": csrf },
        body: JSON.stringify({ atual: data.atual, nova: data.nova }),
      });
      const out = await resp.json().catch(() => ({}));
      if (!resp.ok || out.ok === false) throw new Error(out.erro || `Erro ${resp.status}`);
      csrf = out.csrf || csrf;
      state.passwordForced = false;
      $("#password-dialog").close();
      toast("Senha alterada.");
      loadMe().then(loadConversations).catch(() => {});
    } catch (err) {
      $("#password-error").textContent = err.message;
    }
  }

  async function loadStatus() {
    try {
      const st = await api("/status");
      const note = $("#busy-note");
      if (!st.gerador_online) {
        note.textContent = "O estúdio está pausado no momento. Seus pedidos ficam guardados e começam assim que ele voltar.";
      } else if (st.fila >= 2) {
        note.textContent = "Há outros pedidos na frente. Sua imagem começa logo em seguida.";
      } else {
        note.textContent = "";
      }
      note.hidden = !note.textContent;
    } catch (_) { /* aviso é opcional */ }
  }

  async function loadEstimates(force) {
    if (!force && Date.now() - state.estimatesAt < 60000) return;
    try {
      state.estimates = (await api("/estimativas")).tamanhos || {};
      state.estimatesAt = Date.now();
    } catch (_) { /* opcional */ }
    renderEstimate();
  }

  // Tempo estimado do modo escolhido, só quando há medições suficientes (o servidor já filtra).
  function renderEstimate() {
    const box = $("#estimate");
    if (!state.profiles) { box.textContent = ""; return; }
    const aspect = $("#opt-aspect").value || "4:5";
    const size = state.profiles[state.quality].tamanhos[aspect];
    const est = size && state.estimates[`${size[0]}x${size[1]}`];
    box.textContent = est && est.confiavel && est.tempo_s ? `Tempo estimado: ${friendlyDuration(est.tempo_s)}` : "";
  }

  const ADJUSTMENTS = ["Deixe mais iluminada", "Deixe mais realista", "Mude o ângulo da cena", "Deixe as cores mais vibrantes",
    "Troque o fundo por outro"];

  async function quickAdjust(text) {
    $("#input").value = text;
    autoGrow();
    $("#composer").requestSubmit();
  }

  // ------------------------------------------------------------ conversas

  async function loadConversations() {
    const data = await api("/conversations");
    const list = $("#conversation-list");
    list.replaceChildren(
      ...data.conversas.map((c) =>
        el("button", {
          class: "conv-item" + (c.id === state.conversationId ? " active" : ""),
          text: c.title,
          title: c.title,
          onclick: () => openConversation(c.id),
        })
      )
    );
    if (!data.conversas.length) list.append(el("p", { class: "muted", text: "Nenhuma conversa ainda." }));
  }

  async function newConversation() {
    const data = await api("/conversations", { method: "POST", json: {} });
    await openConversation(data.id);
    await loadConversations();
    $("#input").focus();
  }

  async function openConversation(id) {
    state.conversationId = id;
    history.replaceState(null, "", `/studio/?c=${encodeURIComponent(id)}`);
    $("#sidebar").classList.remove("open");
    showGallery(false);
    await refresh(true);
    loadConversations();
  }

  function setMode(mode) {
    state.mode = mode === "ad" ? "ad" : "free";
    $("#layout").dataset.mode = state.mode;
    $("#input").placeholder = state.mode === "ad"
      ? "Ex.: Tomate kg 6,99 — um produto por linha"
      : "Descreva a imagem… (ou peça um ajuste da última)";
  }

  async function refresh(scroll) {
    if (!state.conversationId) return;
    const data = await api(`/conversations/${state.conversationId}`);
    setMode(data.conversa.modo);
    state.jobs = Object.fromEntries(data.jobs.map((j) => [j.id, j]));
    state.latestDoneId = (data.jobs.find((j) => j.status === "done" && j.tipo === "free_image") || {}).id;
    renderMessages(data.mensagens, scroll);
    if (state.mode === "ad" && !state.saveTimer && !$("#briefing-form").contains(document.activeElement)) {
      renderBriefing(data.briefing, data.pode_gerar, data.motivo);
    }
    renderJobs(data.jobs.filter((j) => j.tipo !== "free_image"));
    const busy = data.mensagens.some((m) => m.status === "pending") || data.jobs.some((j) => ACTIVE.includes(j.status));
    clearTimeout(state.pollTimer);
    if (busy) state.pollTimer = setTimeout(() => refresh(false).catch(() => {}), 2500);
    else loadEstimates(true);
  }

  // ------------------------------------------------------------ mensagens e cartões de geração

  function renderMessages(messages, scroll) {
    const box = $("#messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    $("#empty-state").hidden = messages.length > 1;
    const nodes = messages.map((m) => {
      const bubble = el("div", { class: `msg ${m.role}${m.status === "pending" ? " pending" : ""}` },
        el("div", { class: "msg-text", text: m.texto }));
      if (m.anexos && m.anexos.length) {
        bubble.append(el("div", { class: "msg-attachments" },
          ...m.anexos.map((a) => el("img", { src: assetUrl(a.id), alt: a.kind, loading: "lazy" }))));
      }
      const job = m.job_id && state.jobs[m.job_id];
      if (job) bubble.append(generationCard(job));
      return bubble;
    });
    box.replaceChildren($("#empty-state"), ...nodes);
    if (scroll || nearBottom) box.scrollTop = box.scrollHeight;
  }

  function generationCard(job) {
    const card = el("div", { class: `gen-card ${job.status}` });
    if (job.resultado_asset_id) {
      const img = el("img", { class: "gen-image", src: assetUrl(job.resultado_asset_id), alt: job.prompt || "Imagem gerada" });
      img.addEventListener("click", () => openViewer(job));
      card.append(img);
    } else if (ACTIVE.includes(job.status)) {
      const ph = el("div", { class: "gen-placeholder" });
      if (job.tamanho) ph.style.aspectRatio = `${job.tamanho[0]} / ${job.tamanho[1]}`; // CSSOM (permitido pela CSP)
      const fill = el("span");
      fill.style.width = `${Math.max(3, job.progress)}%`;
      let timeText = "";
      if (job.restante_s != null && job.restante_s > 0) timeText = `Falta ${friendlyDuration(job.restante_s)}`;
      else if (job.estimativa_s && job.status === "queued") timeText = `Tempo estimado: ${friendlyDuration(job.estimativa_s)}`;
      ph.append(
        el("div", { class: "gen-stage", text: job.mensagem }),
        el("div", { class: "bar", role: "progressbar", "aria-valuenow": job.progress, "aria-valuemin": 0, "aria-valuemax": 100 }, fill));
      if (timeText) ph.append(el("div", { class: "muted small", text: timeText }));
      card.append(ph);
    }
    if (job.status === "failed") card.append(el("div", { class: "gen-error", text: job.mensagem }));
    if (job.status === "canceled") card.append(el("div", { class: "muted", text: job.mensagem }));
    if (job.status === "done") {
      const meta = [job.geracao_s ? `Criada em ${fmtSeconds(job.geracao_s)}` : "", job.formato_nome,
        job.modo === "img2img" ? "a partir da sua foto" : ""].filter(Boolean).join(" · ");
      if (meta) card.append(el("div", { class: "gen-meta muted small", text: meta }));
    }

    const actions = el("div", { class: "gen-actions" });
    if (ACTIVE.includes(job.status)) {
      actions.append(button("Cancelar", "x", { class: "btn btn-small btn-danger-ghost", onclick: () => cancelJob(job.id) }));
    }
    if (job.resultado_asset_id) {
      actions.append(
        button("Baixar", "download", { class: "btn btn-small btn-primary", href: assetUrl(job.resultado_asset_id) + "?download=1" }),
        button("Outra versão", "refresh", { class: "btn btn-small btn-ghost", onclick: () => regenerate(job.id) }),
        button("Ajustar", "edit", { class: "btn btn-small btn-ghost", onclick: () => { $("#input").focus(); $("#input").placeholder = "Ex.: deixe mais escuro, troque o fundo por praia…"; } }),
        button("Ampliar", "open", { class: "btn btn-small btn-ghost", onclick: () => openViewer(job) }));
    } else if (["failed", "canceled"].includes(job.status) && job.tipo === "free_image") {
      actions.append(button("Tentar de novo", "refresh", { class: "btn btn-small btn-ghost", onclick: () => regenerate(job.id) }));
    }
    card.append(actions);
    if (job.status === "done" && job.tipo === "free_image" && job.id === state.latestDoneId) {
      card.append(el("div", { class: "suggestions" },
        el("span", { class: "muted small", text: "Quer ajustar?" }),
        ...ADJUSTMENTS.map((text) => el("button", { type: "button", class: "chip-btn chip-sm", text, onclick: () => quickAdjust(text) }))));
    }
    return card;
  }

  function openViewer(job) {
    $("#viewer-img").src = assetUrl(job.resultado_asset_id);
    $("#viewer-prompt").textContent = job.pedido || "";
    $("#viewer").showModal();
  }

  function renderPendingAttachments() {
    const labels = { reference: "Referência", logo: "Logo", product: "Produto" };
    $("#pending-attachments").replaceChildren(
      ...state.pendingAttachments.map((a, i) =>
        el("span", { class: "chip" },
          el("img", { src: assetUrl(a.id), alt: "" }),
          labels[a.kind],
          el("button", {
            type: "button", "aria-label": "Remover anexo", text: "✕",
            onclick: () => { state.pendingAttachments.splice(i, 1); renderPendingAttachments(); },
          })))
    );
    $("#opt-fidelity-wrap").hidden = !state.pendingAttachments.length || state.mode === "ad";
  }

  function currentOptions() {
    return {
      proporcao: $("#opt-aspect").value || null,
      estilo: $("#opt-style").value || "auto",
      qualidade: state.quality,
      fidelidade: $("#opt-fidelity").value,
    };
  }

  async function sendMessage(event) {
    event.preventDefault();
    const input = $("#input");
    const texto = input.value.trim();
    if (!texto && !state.pendingAttachments.length) return;
    if (!state.conversationId) await newConversation();
    const button = $("#btn-send");
    button.disabled = true;
    try {
      await api(`/conversations/${state.conversationId}/messages`, {
        method: "POST",
        json: { texto, anexos: state.pendingAttachments.map((a) => a.id), opcoes: currentOptions() },
      });
      input.value = "";
      autoGrow();
      state.pendingAttachments = [];
      renderPendingAttachments();
      await refresh(true);
      loadConversations();
      loadStatus();
    } catch (err) {
      toast(err.message);
    } finally {
      button.disabled = false;
    }
  }

  async function onFilesChosen(event) {
    const kind = state.mode === "ad" ? $("#attach-kind").value : "reference";
    for (const file of event.target.files) {
      try {
        const asset = await uploadFile(file, kind);
        state.pendingAttachments.push(asset);
        renderPendingAttachments();
      } catch (err) {
        toast(`${file.name}: ${err.message}`);
      }
    }
    event.target.value = "";
  }

  function autoGrow() {
    const input = $("#input");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  async function regenerate(jobId) {
    try {
      await api(`/jobs/${jobId}/regenerate`, { method: "POST" });
      await refresh(true);
    } catch (err) {
      toast(err.message);
    }
  }

  async function cancelJob(id) {
    try {
      await api(`/jobs/${id}/cancel`, { method: "POST" });
      await refresh(false);
    } catch (err) {
      toast(err.message);
    }
  }

  // ------------------------------------------------------------ galeria

  async function showGallery(show) {
    $("#gallery").hidden = !show;
    $("#messages").hidden = show;
    $("#composer").hidden = show;
    if (!show) return;
    $("#sidebar").classList.remove("open");
    const grid = $("#gallery-grid");
    grid.replaceChildren(el("p", { class: "muted", text: "Carregando…" }));
    try {
      const data = await api("/galeria");
      $("#gallery-count").textContent = data.imagens.length
        ? `${data.imagens.length} ${data.imagens.length > 1 ? "imagens" : "imagem"} (as mais recentes primeiro)` : "";
      grid.replaceChildren(...data.imagens.map((job) => {
        const img = el("img", { src: assetUrl(job.resultado_asset_id), alt: job.pedido || "Imagem criada", loading: "lazy" });
        if (job.tamanho) img.style.aspectRatio = `${job.tamanho[0]} / ${job.tamanho[1]}`;
        const open = el("button", { type: "button", class: "tile-open", "aria-label": "Ampliar imagem", onclick: () => openViewer(job) }, img);
        const meta = [job.formato_nome, window.SG.fmtDate(job.criado_em)].filter(Boolean).join(" · ");
        return el("figure", { class: "tile" }, open,
          el("figcaption", { class: "tile-caption" },
            el("p", { class: "tile-prompt", text: job.pedido || (job.tipo === "ad_composite" ? "Encarte" : "Criação") }),
            el("div", { class: "tile-bar" },
              el("span", { class: "muted small", text: meta }),
              el("div", { class: "tile-actions" },
                button("", "download", { class: "icon-btn icon-btn-sm", href: assetUrl(job.resultado_asset_id) + "?download=1", "aria-label": "Baixar", title: "Baixar" }),
                button("", "open", { class: "icon-btn icon-btn-sm", href: `/studio/?c=${encodeURIComponent(job.conversation_id)}`, "aria-label": "Abrir conversa", title: "Abrir conversa" })))));
      }));
      if (!data.imagens.length) {
        grid.append(el("div", { class: "gallery-empty" }, icon("image", 32), el("p", { text: "Nenhuma imagem gerada ainda." }),
          button("Criar a primeira", "sparkle", { class: "btn btn-primary", onclick: () => { showGallery(false); $("#input").focus(); } })));
      }
    } catch (err) {
      grid.replaceChildren(el("p", { class: "muted", text: err.message }));
    }
  }

  // ------------------------------------------------------------ briefing (encartes)

  const FIELDS = ["formato", "titulo", "loja", "validade", "tema", "cenario", "observacoes"];

  function renderBriefing(briefing, podeGerar, motivo) {
    state.briefing = briefing;
    $("#briefing-empty").hidden = true;
    const form = $("#briefing-form");
    form.hidden = false;
    for (const field of FIELDS) form.elements[field].value = briefing[field] || "";

    const logo = $("#logo-thumb");
    logo.replaceChildren(briefing.logo_asset_id ? el("img", { src: assetUrl(briefing.logo_asset_id), alt: "Logo" }) : "sem logo");

    $("#products").replaceChildren(...briefing.produtos.map(productRow));
    $("#pendencias").replaceChildren(...(briefing.pendencias || []).map((p) => el("li", { text: p })));
    $("#btn-generate").disabled = !podeGerar;
    $("#generate-hint").textContent = podeGerar
      ? "A IA vai criar só o cenário. Fotos, preços e logo entram exatamente como acima."
      : motivo || "";
  }

  function productRow(product, index) {
    const thumb = el("button", {
      type: "button", class: "thumb", title: "Escolher foto",
      onclick: () => openPhotoDialog(index),
    }, product.asset_id ? el("img", { src: assetUrl(product.asset_id), alt: product.nome }) : "foto");
    const input = (name, placeholder, cls) => el("input", {
      class: cls || "", value: product[name] || "", placeholder, maxlength: name === "nome" ? 80 : 20,
      "data-index": index, "data-field": name, "aria-label": placeholder,
    });
    return el("div", { class: "product" },
      thumb,
      el("div", { class: "product-fields" },
        input("nome", "Nome do produto", "name"),
        input("preco", "Preço"),
        input("preco_de", "Preço “de”"),
        input("unidade", "Unid.")),
      el("button", {
        type: "button", class: "remove", "aria-label": "Remover produto", text: "✕",
        onclick: () => { state.briefing.produtos.splice(index, 1); saveBriefing(true); },
      }));
  }

  function collectBriefing() {
    const form = $("#briefing-form");
    const briefing = { ...state.briefing };
    for (const field of FIELDS) briefing[field] = form.elements[field].value;
    form.querySelectorAll(".product-fields input").forEach((input) => {
      briefing.produtos[Number(input.dataset.index)][input.dataset.field] = input.value;
    });
    return briefing;
  }

  function scheduleSave() {
    $("#save-state").textContent = "editando…";
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => saveBriefing(false), 900);
  }

  async function saveBriefing(rerender) {
    clearTimeout(state.saveTimer);
    state.saveTimer = null;
    if (!state.conversationId || !state.briefing) return;
    const briefing = rerender ? state.briefing : collectBriefing();
    try {
      const data = await api(`/conversations/${state.conversationId}/briefing`, { method: "PUT", json: { briefing } });
      $("#save-state").textContent = "salvo";
      if (rerender || !$("#briefing-form").contains(document.activeElement)) {
        renderBriefing(data.briefing, data.pode_gerar, data.motivo);
      } else {
        state.briefing = { ...data.briefing };
        $("#pendencias").replaceChildren(...data.briefing.pendencias.map((p) => el("li", { text: p })));
        $("#btn-generate").disabled = !data.pode_gerar;
        $("#generate-hint").textContent = data.pode_gerar ? "" : data.motivo;
      }
    } catch (err) {
      $("#save-state").textContent = "erro ao salvar";
      toast(err.message);
    }
  }

  async function addProduct() {
    if (!state.briefing) return;
    state.briefing = collectBriefing();
    state.briefing.produtos.push({ nome: "Novo produto", preco: "", preco_de: "", unidade: "", ean: "", asset_id: null });
    await saveBriefing(true);
  }

  async function uploadLogo(event) {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    try {
      const asset = await uploadFile(file, "logo");
      state.briefing = collectBriefing();
      state.briefing.logo_asset_id = asset.id;
      await saveBriefing(true);
    } catch (err) {
      toast(err.message);
    }
  }

  function renderJobs(jobs) {
    $("#jobs").replaceChildren(...jobs.map(generationCard));
    if (!jobs.length) $("#jobs").append(el("p", { class: "muted", text: "Nenhum encarte gerado ainda." }));
  }

  async function generate() {
    await saveBriefing(false);
    try {
      await api(`/conversations/${state.conversationId}/jobs`, { method: "POST" });
      toast("Pronto! Vou criar o seu encarte.");
      await refresh(false);
    } catch (err) {
      toast(err.message);
    }
  }

  // ------------------------------------------------------------ fotos de produto

  function openPhotoDialog(index) {
    state.briefing = collectBriefing();
    state.photoTarget = index;
    const product = state.briefing.produtos[index];
    $("#photo-query").value = product.ean || product.nome || "";
    $("#photo-results").replaceChildren();
    $("#photo-dialog").showModal();
    if ($("#photo-query").value) searchPhotos();
  }

  async function searchPhotos() {
    const query = $("#photo-query").value.trim();
    if (!query) return;
    const isEan = /^\d{8,14}$/.test(query);
    const box = $("#photo-results");
    box.replaceChildren(el("p", { class: "muted", text: "Buscando…" }));
    try {
      const data = await api(`/products/search?${isEan ? "ean" : "q"}=${encodeURIComponent(query)}`);
      if (!data.resultados.length) {
        box.replaceChildren(el("p", { class: "muted", text: "Nada encontrado. Envie uma foto própria." }));
        return;
      }
      box.replaceChildren(...data.resultados.map((r) =>
        el("div", { class: "photo-card" },
          el("img", { src: r.image_url, alt: r.nome, loading: "lazy", referrerpolicy: "no-referrer" }),
          el("b", { text: r.nome || "(sem nome)" }),
          el("span", { class: "muted", text: [r.marca, r.quantidade].filter(Boolean).join(" · ") }),
          el("span", { class: "muted", text: `${r.fonte} · ${r.license}` }),
          el("button", { type: "button", class: "btn btn-small btn-primary", text: "Usar esta foto", onclick: () => importPhoto(r) }))));
    } catch (err) {
      box.replaceChildren(el("p", { class: "muted", text: err.message }));
    }
  }

  async function importPhoto(result) {
    try {
      const data = await api("/products/import", {
        method: "POST",
        json: { image_url: result.image_url, license: result.license, attribution: result.attribution },
      });
      setProductPhoto(data.asset.id, result.ean);
    } catch (err) {
      toast(err.message);
    }
  }

  async function uploadProductPhoto(event) {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    try {
      const asset = await uploadFile(file, "product");
      setProductPhoto(asset.id, null);
    } catch (err) {
      toast(err.message);
    }
  }

  function setProductPhoto(assetId, ean) {
    const product = state.briefing.produtos[state.photoTarget];
    if (!product) return;
    product.asset_id = assetId;
    if (ean && !product.ean) product.ean = ean;
    $("#photo-dialog").close();
    saveBriefing(true);
  }

  // ------------------------------------------------------------ eventos

  function setView(view) {
    $("#layout").dataset.view = view;
    document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  }

  async function logout() {
    try {
      await fetch("/studio/auth/logout", { method: "POST", credentials: "same-origin", headers: { "X-SG-CSRF": csrf } });
    } finally {
      location.assign("/studio/entrar");
    }
  }

  function decorate() {
    const put = (sel, name, label) => { const n = $(sel); n.prepend(icon(name, 18)); if (label) n.append(el("span", { text: label })); };
    put("#btn-menu", "menu");
    put("#btn-new", "plus", "Nova criação");
    put("#btn-gallery", "grid", "Galeria");
    put("#btn-gallery-close", "arrowLeft", "Voltar ao chat");
    put("#attach-label", "clip");
    put("#btn-send", "sparkle", "Criar");
    put("#menu-admin", "shield", "Painel do administrador");
    put("#menu-password", "key", "Trocar senha");
    put("#btn-logout", "logout", "Sair");
    $("#user-btn").append(icon("chevron", 16));
  }

  function bind() {
    decorate();
    $("#user-btn").addEventListener("click", (e) => { e.stopPropagation(); toggleUserMenu(); });
    document.addEventListener("click", (e) => { if (!$("#user-menu").contains(e.target)) toggleUserMenu(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleUserMenu(false); });
    $("#menu-password").addEventListener("click", () => openPasswordDialog(false));
    $("#password-form").addEventListener("submit", submitPassword);
    $("#password-cancel").addEventListener("click", () => $("#password-dialog").close());
    $("#password-dialog").addEventListener("cancel", (e) => { if (state.passwordForced) e.preventDefault(); });
    $("#composer").addEventListener("submit", sendMessage);
    $("#input").addEventListener("input", autoGrow);
    $("#input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $("#composer").requestSubmit();
      }
    });
    $("#file-input").addEventListener("change", onFilesChosen);
    $("#btn-new").addEventListener("click", () => newConversation().catch((e) => toast(e.message)));
    $("#btn-gallery").addEventListener("click", () => showGallery(true));
    $("#btn-gallery-close").addEventListener("click", () => showGallery(false));
    $("#btn-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    $("#examples").addEventListener("click", (e) => {
      if (!e.target.matches(".chip-btn")) return;
      $("#input").value = e.target.textContent;
      autoGrow();
      $("#input").focus();
    });
    $("#opt-aspect").addEventListener("change", renderEstimate);
    document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
      state.quality = b.dataset.quality;
      document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    }));
    $("#briefing-form").addEventListener("input", scheduleSave);
    $("#briefing-form").addEventListener("submit", (e) => e.preventDefault());
    $("#btn-add-product").addEventListener("click", addProduct);
    $("#logo-input").addEventListener("change", uploadLogo);
    $("#btn-generate").addEventListener("click", generate);
    $("#btn-photo-search").addEventListener("click", searchPhotos);
    $("#photo-query").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchPhotos(); } });
    $("#photo-upload").addEventListener("change", uploadProductPhoto);
    $("#viewer").addEventListener("click", (e) => { if (e.target.id === "viewer") $("#viewer").close(); });
    document.querySelectorAll(".tabbar button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
    $("#btn-logout").addEventListener("click", logout);
  }

  async function start() {
    bind();
    try {
      await loadMe();
      loadStatus();
      setInterval(loadStatus, 20000);
      loadEstimates(true);
      await loadConversations();
      const id = new URLSearchParams(location.search).get("c");
      if (id) await openConversation(id);
      else if (location.hash === "#galeria") showGallery(true);
    } catch (err) {
      toast(err.message);
    }
  }

  start();
})();
