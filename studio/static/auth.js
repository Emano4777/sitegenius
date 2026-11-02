// SiteGenius Studio — cadastro, login e recuperação de senha.
(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="sg-csrf"]').content;
  const root = document.getElementById("auth");
  const msg = document.getElementById("auth-msg");
  // O token de recuperação vem no fragmento (#t=...), que não é enviado ao servidor nem a terceiros.
  const resetToken = new URLSearchParams(location.hash.slice(1)).get("t") || "";
  if (resetToken) history.replaceState(null, "", location.pathname);

  function show(panel) {
    for (const section of root.querySelectorAll("[data-panel]")) section.hidden = section.dataset.panel !== panel;
    say("");
    const first = root.querySelector(`[data-panel="${panel}"] input`);
    if (first) first.focus();
  }

  function say(text, kind = "") {
    msg.textContent = text;
    msg.className = "auth-msg" + (kind ? " " + kind : "");
  }

  async function post(path, body) {
    const resp = await fetch("/studio/auth/" + path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-SG-CSRF": csrf },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await resp.json(); } catch (_) { /* sem JSON */ }
    if (!resp.ok || data.ok === false) throw new Error(data.erro || `Erro ${resp.status}`);
    return data;
  }

  function bind(formId, path, extra = () => ({})) {
    const form = document.getElementById(formId);
    if (!form) return;  // painel desativado (ex.: recuperação por e-mail sem Resend configurado)
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      say("Aguarde…");
      try {
        const data = await post(path, { ...Object.fromEntries(new FormData(form)), ...extra() });
        if (data.redirect) { location.assign(data.redirect); return; }
        say(data.mensagem || "Pronto.", "ok");
        form.reset();
      } catch (err) {
        say(err.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  }

  for (const link of root.querySelectorAll("[data-go]")) {
    link.addEventListener("click", (event) => { event.preventDefault(); show(link.dataset.go); });
  }
  bind("form-entrar", "login");
  bind("form-esqueci", "esqueci-senha");
  bind("form-redefinir", "redefinir-senha", () => ({ token: resetToken }));

  if (root.dataset.view === "redefinir") {
    show("redefinir");
    if (!resetToken) say("Link inválido. Peça um novo em “Esqueci a senha”.", "error");
  } else {
    show(location.hash === "#esqueci" && document.getElementById("form-esqueci") ? "esqueci" : "entrar");
  }
})();
