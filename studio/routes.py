"""Rotas do SiteGenius Studio (usuário em /studio, workers em /studio/worker)."""
import hashlib
import hmac
import os
import re
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    session,
)

from . import accounts
from . import briefing as bf
from . import creative
from . import storage
from .db import dumps, get_conn, loads, new_id, now
from .products import lookup_ean, search_name
from .security import (
    ImageRejected,
    UnsafeUrl,
    rate_limited,
    safe_download,
    sanitize_image,
    verify_worker_request,
)

bp = Blueprint(
    "studio",
    __name__,
    url_prefix="/studio",
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

ASSET_KINDS = {"reference", "product", "logo"}
UUID_RE = re.compile(r"^[0-9a-f\-]{36}$")
# Resolução do cenário por formato: até ~786k pixels, o que o Acer (6 GB VRAM) já mostrou suportar.
SCENE_SIZES = {"feed_4x5": (768, 960), "quadrado": (832, 832), "story": (576, 1024), "a4": (704, 1024)}


def cfg():
    return current_app.extensions["sg_studio"]["config"]


def render_page(name, **context):
    """Renderiza sem os context processors do app.py (que abrem conexão no banco a cada página)."""
    return current_app.jinja_env.get_template(name).render(**context)


def error(message, status=400):
    return jsonify({"ok": False, "erro": message}), status


def valid_id(value):
    if not value or not UUID_RE.match(value):
        abort(404)
    return value


# ---------------------------------------------------------------- autenticação do usuário


SESSION_KEYS = ("sg_uid", "sg_sv", "sg_auth_at")
MAINTENANCE = "O estúdio está em manutenção. Tente novamente em alguns minutos."
PASSWORD_CHANGE_ALLOWED = {"studio.studio_page", "studio.api_me", "studio.api_status", "studio.auth_change_password",
                           "studio.auth_logout"}


def current_user_id():
    """Sessão própria do Studio (sg_uid); o login antigo do site (user_id) não vale aqui."""
    if "sg_user" in g:
        return g.sg_user["id"] if g.sg_user else None
    g.sg_user = None
    uid = session.get("sg_uid")
    if uid and now() - int(session.get("sg_auth_at", 0)) < accounts.SESSION_MAX_AGE_S:
        user = accounts.get_user(get_conn(), int(uid))
        # session_version muda ao trocar a senha: derruba as sessões antigas.
        if user and user["session_version"] == session.get("sg_sv"):
            g.sg_user = user
    if not g.sg_user and cfg().dev_user_id:  # só fora do Vercel (ver config.dev_user_id)
        g.sg_user = accounts.get_user(get_conn(), cfg().dev_user_id) or {
            "id": cfg().dev_user_id, "name": "Dev", "email": "dev@localhost"}
    return g.sg_user["id"] if g.sg_user else None


def start_session(user):
    for key in SESSION_KEYS + ("sg_csrf",):
        session.pop(key, None)  # novo CSRF a cada login (evita fixação)
    session.permanent = True
    session.update(sg_uid=user["id"], sg_sv=user["session_version"], sg_auth_at=now())
    return csrf_token()


def end_session():
    for key in SESSION_KEYS + ("sg_csrf",):
        session.pop(key, None)


def csrf_token():
    if "sg_csrf" not in session:
        session["sg_csrf"] = secrets.token_urlsafe(32)
    return session["sg_csrf"]


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_app.extensions["sg_studio"].get("insecure_reason"):
            current_app.logger.error("studio desativado: %s", current_app.extensions["sg_studio"]["insecure_reason"])
            return error(MAINTENANCE, 503)
        user_id = current_user_id()
        if not user_id:
            if request.path.startswith("/studio/api"):
                return error("Faça login para usar o Studio.", 401)
            return redirect("/studio/entrar", 302)
        problem = csrf_problem()
        if problem:
            return problem
        g.user_id = user_id
        if (g.sg_user or {}).get("must_change_password") and request.endpoint not in PASSWORD_CHANGE_ALLOWED:
            if not request.path.startswith("/studio/api") and request.method == "GET":
                return redirect("/studio/", 302)  # o Studio abre a tela de criar a senha
            return error("Troque a senha provisória para continuar.", 403)
        return view(*args, **kwargs)

    return wrapper


def csrf_problem():
    if request.method in ("GET", "HEAD"):
        return None
    sent = request.headers.get("X-SG-CSRF", "")
    if not sent or not hmac.compare_digest(sent, session.get("sg_csrf", "")):
        return error("Sua sessão expirou. Recarregue a página para continuar.", 403)
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return error("Não foi possível concluir. Recarregue a página e tente de novo.", 403)
    return None


def studio_enabled():
    reason = current_app.extensions["sg_studio"].get("insecure_reason")
    if reason:
        current_app.logger.error("studio desativado: %s", reason)
        return error(MAINTENANCE, 503)
    return None


def client_ip():
    # No Vercel o IP real do cliente vem em x-forwarded-for (preenchido pela própria plataforma).
    forwarded = request.headers.get("X-Forwarded-For", "") if cfg().is_vercel else ""
    return (forwarded.split(",")[0].strip() or request.remote_addr or "?")[:64]


def limit(bucket, max_calls, window_s):
    if rate_limited(get_conn(), f"{bucket}:{g.user_id}", max_calls, window_s):
        return error("Muitas ações em pouco tempo. Aguarde alguns segundos e tente de novo.", 429)
    return None


@bp.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob: https://images.openfoodfacts.org "
        "https://static.openfoodfacts.org https://images.openbeautyfacts.org https://static.openbeautyfacts.org; "
        "script-src 'self'; style-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    if request.path.startswith("/studio/api") or request.path.startswith("/studio/worker"):
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store")
    return resp


# ---------------------------------------------------------------- consultas auxiliares


def get_conversation(conversation_id):
    conv = get_conn().one(
        "SELECT * FROM sg_conversations WHERE id = ? AND user_id = ?", (valid_id(conversation_id), g.user_id)
    )
    if not conv:
        abort(404)
    return conv


def owned_asset_ids(user_id, ids):
    ids = [i for i in set(ids) if i and UUID_RE.match(str(i))]
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    rows = get_conn().all(f"SELECT id FROM sg_assets WHERE user_id = ? AND id IN ({marks})", [user_id, *ids])
    return {r["id"] for r in rows}


def briefing_asset_ids(data):
    data = data if isinstance(data, dict) else {}
    ids = [p.get("asset_id") for p in data.get("produtos") or [] if isinstance(p, dict)]
    ids += [data.get("logo_asset_id")] + list(data.get("referencias") or [])
    return [str(i) for i in ids if i]


def user_texts(conversation_id):
    rows = get_conn().all(
        "SELECT content FROM sg_messages WHERE conversation_id = ? AND role = 'user' ORDER BY created_at",
        (conversation_id,),
    )
    return [r["content"] for r in rows]


def confirmed_prices(previous):
    """Preços já validados no briefing anterior continuam valendo como texto do usuário."""
    out = []
    for p in (previous or {}).get("produtos") or []:
        out += [p.get("preco") or "", p.get("preco_de") or ""]
    return out


def merge_previous_assets(new, previous):
    if not previous:
        return new
    by_name = {bf.strip_accents(p["nome"].lower()): p for p in previous.get("produtos") or []}
    for product in new.get("produtos") or []:
        old = by_name.get(bf.strip_accents(str(product.get("nome", "")).lower()))
        if old and not product.get("asset_id"):
            product["asset_id"] = old.get("asset_id")
    new.setdefault("logo_asset_id", None)
    if not new.get("logo_asset_id"):
        new["logo_asset_id"] = previous.get("logo_asset_id")
    new["referencias"] = list(dict.fromkeys((previous.get("referencias") or []) + (new.get("referencias") or [])))
    return new


def apply_attachments(briefing, attachments):
    for att in attachments:
        if att["kind"] == "logo":
            briefing["logo_asset_id"] = att["id"]
        elif att["kind"] == "reference":
            briefing["referencias"] = list(dict.fromkeys((briefing.get("referencias") or []) + [att["id"]]))
    return briefing


def add_message(conversation_id, role, content, attachments=None, status="ok", message_id=None, job_id=None):
    conn = get_conn()
    seq = conn.one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM sg_messages WHERE conversation_id = ?", (conversation_id,))["n"]
    conn.run(
        "INSERT INTO sg_messages (id, conversation_id, seq, role, content, attachments, status, job_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id or new_id(), conversation_id, seq, role, content, dumps(attachments or []), status, job_id, now()),
    )


def set_message(message_id, conversation_id, content, job_id=None):
    get_conn().run(
        "UPDATE sg_messages SET content = ?, status = 'ok', job_id = ? WHERE id = ? AND conversation_id = ?",
        (content, job_id, message_id, conversation_id),
    )


def save_briefing(conversation_id, briefing):
    get_conn().run(
        "UPDATE sg_conversations SET briefing = ?, updated_at = ? WHERE id = ?",
        (dumps(briefing), now(), conversation_id),
    )


def job_view(job):
    result = loads(job.get("result"), {}) or {}
    payload = loads(job.get("payload"), {}) or {}
    finished = job.get("finished_at")
    spec = payload.get("spec") or {}
    estimate = estimate_for_job(job, payload)
    elapsed = (now() - job["started_at"]) if job.get("started_at") and not finished else None
    return {
        "id": job["id"],
        "kind": job["kind"],
        "tipo": job.get("gen_type") or "ad_composite",
        "status": job["status"],
        "progress": job["progress"],
        # Texto para o cliente: natural, sem termos técnicos (o técnico fica em status_message/error, só no admin).
        "mensagem": friendly_status(job, estimate),
        "erro": FRIENDLY_FAILURE if job["status"] == "failed" else "",
        "pedido": spec.get("prompt_original") or "",
        "formato_nome": creative.ASPECTS.get(job.get("aspect_ratio") or "", {}).get("nome")
                        or bf.FORMATS.get(job.get("aspect_ratio") or "", {}).get("nome", ""),
        "estimativa_s": estimate,
        # Restante: primeiro o cálculo real do agente (velocidade dos passos desta imagem); senão, o histórico.
        "restante_s": remaining_seconds(job, estimate, elapsed),
        "resultado_asset_id": result.get("asset_id"),
        "prompt": job.get("prompt_visual") or "",
        "estilo": job.get("style") or "",
        "proporcao": job.get("aspect_ratio") or "",
        "tamanho": payload.get("size") or payload.get("scene_size"),
        "modo": job.get("render_mode") or "txt2img",
        "seed": job.get("seed"),
        "tempos": loads(job.get("timings"), {}) or {},
        "duracao_s": (finished - job["created_at"]) if finished else None,
        # Espera na fila (até o Acer pegar) separada do tempo de geração de fato.
        "espera_s": (job["started_at"] - job["created_at"]) if job.get("started_at") else None,
        "geracao_s": (finished - job["started_at"]) if finished and job.get("started_at") else None,
        "passos": payload.get("steps"),
        "qualidade": (payload.get("spec") or {}).get("quality"),
        "conversation_id": job.get("conversation_id"),
        "criado_em": job["created_at"],
        "atualizado_em": job["updated_at"],
    }


def message_view(msg):
    return {
        "id": msg["id"],
        "role": msg["role"],
        "texto": msg["content"],
        "anexos": loads(msg.get("attachments"), []),
        "status": msg["status"],
        "job_id": msg.get("job_id"),
        "criado_em": msg["created_at"],
    }


WORKER_ONLINE_S = 45


def workers_online(kind):
    ids = [wid for wid, w in cfg().workers.items() if w["kind"] == kind]
    if not ids:
        return False
    marks = ",".join("?" for _ in ids)
    row = get_conn().one(f"SELECT MAX(last_seen) AS t FROM sg_worker_status WHERE worker_id IN ({marks})", ids)
    return bool(row and row["t"] and now() - row["t"] <= WORKER_ONLINE_S)


def interpreter_available():
    mode = cfg().interpreter_mode
    if mode == "rules" or not any(w["kind"] == "interpret" for w in cfg().workers.values()):
        return False
    return mode == "queue" or workers_online("interpret")


# ---------------------------------------------------------------- páginas


@bp.get("")
def studio_no_slash():
    # Evita que /studio caia na rota genérica /<subdomain> do site.
    return redirect("/studio/", 308)


@bp.get("/")
@login_required
def studio_page():
    return render_page("studio/index.html", csrf_token=csrf_token())


# ---------------------------------------------------------------- contas (cadastro, login, senha)


@bp.get("/entrar")
def auth_page():
    if not current_app.extensions["sg_studio"].get("insecure_reason") and current_user_id():
        return redirect("/studio/", 302)
    return render_page("studio/auth.html", csrf_token=csrf_token(), view="entrar", reset_enabled=email_enabled())


@bp.get("/redefinir-senha")
def reset_page():
    return render_page("studio/auth.html", csrf_token=csrf_token(), view="redefinir", reset_enabled=email_enabled())


def email_enabled():
    return bool(os.getenv("SG_RESEND_API_KEY") and os.getenv("SG_MAIL_FROM"))


def auth_guard(bucket, max_calls, window_s, key=""):
    problem = studio_enabled() or csrf_problem()
    if problem:
        return problem
    conn = get_conn()
    if rate_limited(conn, f"{bucket}:ip:{client_ip()}", max_calls, window_s) or (
        key and rate_limited(conn, f"{bucket}:k:{hashlib.sha256(key.encode()).hexdigest()[:32]}", max_calls, window_s)
    ):
        return error("Muitas tentativas. Aguarde alguns minutos e tente novamente.", 429)
    return None


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.post("/auth/cadastro")
def auth_register():
    # As contas são criadas pelo administrador (painel /studio/admin), que define os limites de uso.
    return error("O cadastro é feito pelo administrador do SiteGenius Studio.", 403)


@bp.post("/auth/trocar-senha")
@login_required
def auth_change_password():
    data = json_body()
    if rate_limited(get_conn(), f"chpw:{g.user_id}", 10, 900):
        return error("Muitas tentativas. Aguarde alguns minutos.", 429)
    try:
        user = accounts.change_password(get_conn(), g.user_id, str(data.get("atual") or ""), data.get("nova"))
    except accounts.AccountError as exc:
        return error(str(exc))
    # session_version mudou: renova a sessão atual (novo CSRF) e derruba as outras.
    return jsonify({"ok": True, "csrf": start_session(user)})


@bp.post("/auth/login")
def auth_login():
    data = json_body()
    email = str(data.get("email") or "").strip().lower()
    problem = auth_guard("login", 10, 900, key=email)
    if problem:
        return problem
    user = accounts.authenticate(get_conn(), email, str(data.get("senha") or ""))
    if not user:
        return error("E-mail ou senha incorretos.", 401)
    return jsonify({"ok": True, "redirect": "/studio/", "csrf": start_session(user)})


@bp.post("/auth/logout")
def auth_logout():
    problem = csrf_problem()
    if problem:
        return problem
    end_session()
    return jsonify({"ok": True, "redirect": "/studio/entrar"})


def public_base_url():
    # Nunca usa o Host da requisição no Vercel (o link de recuperação não pode apontar para outro domínio).
    base = os.getenv("SG_PUBLIC_BASE_URL", "").strip()
    if base:
        return base.rstrip("/")
    if os.getenv("VERCEL_URL"):
        return "https://" + os.environ["VERCEL_URL"]
    return request.url_root.rstrip("/")


@bp.post("/auth/esqueci-senha")
def auth_forgot():
    data = json_body()
    email = str(data.get("email") or "").strip().lower()
    problem = auth_guard("forgot", 5, 3600, key=email)
    if problem:
        return problem
    user, token = accounts.create_reset_token(get_conn(), email)
    if user:
        # Token no fragmento (#): não vai para logs de servidor nem para o Referer.
        link = f"{public_base_url()}/studio/redefinir-senha#t={token}"
        if not accounts.send_reset_email(user["email"], user["name"], link) and not cfg().is_vercel:
            current_app.logger.warning("[dev] link de recuperação: %s", link)
    # Mesma resposta sempre: não revela se o e-mail tem conta.
    return jsonify({"ok": True, "mensagem": "Se o e-mail tiver conta, enviamos um link para criar uma nova senha."})


@bp.post("/auth/redefinir-senha")
def auth_reset():
    data = json_body()
    problem = auth_guard("reset", 10, 900)
    if problem:
        return problem
    try:
        user = accounts.reset_password(get_conn(), str(data.get("token") or ""), data.get("senha"))
    except accounts.AccountError as exc:
        return error(str(exc))
    return jsonify({"ok": True, "redirect": "/studio/", "csrf": start_session(user)})


# ---------------------------------------------------------------- API do usuário


@bp.get("/api/me")
@login_required
def api_me():
    return jsonify(
        {
            "ok": True,
            "user_id": g.user_id,
            "nome": (g.get("sg_user") or {}).get("name", ""),
            "email": (g.get("sg_user") or {}).get("email", ""),
            "admin": bool((g.get("sg_user") or {}).get("is_admin")),
            "trocar_senha": bool((g.get("sg_user") or {}).get("must_change_password")),
            "uso": user_usage(g.user_id),
            "interpretacao": "ia" if interpreter_available() else "regras",
            "formatos": {k: v["nome"] for k, v in bf.FORMATS.items()},
            # Listas (não dicts): o jsonify ordena chaves e bagunçaria a ordem das opções.
            "proporcoes": [[k, v["nome"]] for k, v in creative.ASPECTS.items()],
            "estilos": [[k, v[0]] for k, v in creative.STYLES.items()],
            "perfis": creative.sizes_table(),
            "csrf": csrf_token(),
        }
    )


@bp.get("/api/status")
@login_required
def api_status():
    queued = get_conn().one(
        "SELECT COUNT(*) AS n FROM sg_jobs WHERE kind = 'render' AND status IN ('queued', 'claimed', 'running')"
    )["n"]
    return jsonify({
        "ok": True,
        "interpretacao": "ia" if interpreter_available() else "regras",
        "gerador_online": workers_online("render"),
        "fila": queued,
    })


@bp.get("/api/estimativas")
@login_required
def api_estimates():
    """Tempo real de geração por tamanho (início ao fim, sem a espera). Só é "confiável" com MIN_SAMPLES medições.

    Gerações com o pedido idêntico já em memória (ex.: "outra versão") são bem mais rápidas e ficam de fora,
    para a estimativa de um pedido novo não ser otimista demais.
    """
    rows = get_conn().all(
        "SELECT payload, started_at, finished_at, timings FROM sg_jobs WHERE kind = 'render' AND status = 'done' "
        "AND finished_at IS NOT NULL AND started_at IS NOT NULL ORDER BY finished_at DESC LIMIT 300"
    )
    buckets = {}
    for row in rows:
        payload = loads(row["payload"], {}) or {}
        size = payload.get("size") or payload.get("scene_size")
        if not size or payload.get("render_mode") == "img2img":
            continue
        timings = loads(row["timings"], {}) or {}
        if "encode_s" in timings and timings["encode_s"] < 1.0:
            continue
        entry = buckets.setdefault(f"{size[0]}x{size[1]}", {"total": [], "gpu": []})
        if len(entry["total"]) < 20:
            entry["total"].append(row["finished_at"] - row["started_at"])
            if isinstance(timings.get("comfy_s"), (int, float)):
                entry["gpu"].append(timings["comfy_s"])
    sizes = {
        k: {"tempo_s": _median(v["total"]), "mediana_gpu_s": _median(v["gpu"]), "amostras": len(v["total"]),
            "confiavel": len(v["total"]) >= MIN_SAMPLES, "fonte": "geracoes"}
        for k, v in buckets.items()
    }
    # Benchmark do Acer (execuções quentes com pedido novo) para tamanhos ainda sem gerações reais suficientes.
    bench = {}
    for row in get_conn().all(
        "SELECT size, steps, comfy_s, encode_s FROM sg_benchmarks WHERE cold = 0 AND comfy_s IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 300"
    ):
        if row["encode_s"] is not None and row["encode_s"] < 1.0:
            continue
        bench.setdefault(row["size"], {}).setdefault(row["steps"], []).append(row["comfy_s"])
    wanted_steps = {p["steps"] for p in creative.render_profiles().values()}
    for size, by_steps in bench.items():
        if size in sizes and sizes[size]["confiavel"]:
            continue
        steps = next((s for s in by_steps if s in wanted_steps), None) or max(by_steps, key=lambda s: len(by_steps[s]))
        values = by_steps[steps]
        median = _median(values)
        sizes[size] = {"tempo_s": median + 5 if median is not None else None, "mediana_gpu_s": median,
                       "amostras": len(values), "confiavel": len(values) >= MIN_SAMPLES, "fonte": "benchmark", "passos": steps}
    return jsonify({"ok": True, "tamanhos": sizes})


@bp.get("/api/galeria")
@login_required
def api_gallery():
    rows = get_conn().all(
        "SELECT * FROM sg_jobs WHERE user_id = ? AND kind = 'render' AND status = 'done' ORDER BY finished_at DESC LIMIT 60",
        (g.user_id,),
    )
    return jsonify({"ok": True, "imagens": [job_view(r) for r in rows]})


@bp.get("/api/conversations")
@login_required
def api_conversations():
    rows = get_conn().all(
        "SELECT id, title, updated_at FROM sg_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 100",
        (g.user_id,),
    )
    return jsonify({"ok": True, "conversas": rows})


@bp.post("/api/conversations")
@login_required
def api_create_conversation():
    blocked = limit("conv", 30, 3600)
    if blocked:
        return blocked
    conv_id, ts = new_id(), now()
    title = bf._clean_text((request.get_json(silent=True) or {}).get("titulo"), 80) or NEW_TITLE
    get_conn().run(
        "INSERT INTO sg_conversations (id, user_id, title, briefing, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conv_id, g.user_id, title, dumps(bf.normalize_briefing({})), "free", ts, ts),
    )
    add_message(
        conv_id,
        "assistant",
        "Olá! Descreva a imagem que você quer: o assunto, o clima e, se quiser, o estilo e o formato. "
        "Pode anexar uma foto de referência. Para encartes com produtos e preços, é só pedir um encarte.",
    )
    return jsonify({"ok": True, "id": conv_id}), 201


@bp.get("/api/conversations/<conversation_id>")
@login_required
def api_conversation(conversation_id):
    conv = get_conversation(conversation_id)
    resolve_stale_interpretation(conv)
    conv = get_conversation(conversation_id)
    conn = get_conn()
    messages = conn.all("SELECT * FROM sg_messages WHERE conversation_id = ? ORDER BY seq", (conv["id"],))
    jobs = conn.all(
        "SELECT * FROM sg_jobs WHERE conversation_id = ? AND kind = 'render' ORDER BY created_at DESC LIMIT 50",
        (conv["id"],),
    )
    briefing = loads(conv["briefing"]) or bf.normalize_briefing({})
    ok, motivo = bf.ready_for_render(briefing)
    return jsonify(
        {
            "ok": True,
            "conversa": {"id": conv["id"], "titulo": conv["title"], "modo": conv.get("mode") or "free"},
            "mensagens": [message_view(m) for m in messages],
            "briefing": briefing,
            "pode_gerar": ok,
            "motivo": motivo,
            "jobs": [job_view(j) for j in jobs],
        }
    )


@bp.post("/api/conversations/<conversation_id>/messages")
@login_required
def api_send_message(conversation_id):
    blocked = limit("msg", 30, 60)
    if blocked:
        return blocked
    conv = get_conversation(conversation_id)
    data = request.get_json(silent=True) or {}
    texto = str(data.get("texto") or "").strip()[:4000]
    attachment_ids = [str(a) for a in (data.get("anexos") or [])][:8]
    if not texto and not attachment_ids:
        return error("Mensagem vazia.")
    conn = get_conn()
    attachments = []
    if attachment_ids:
        owned = owned_asset_ids(g.user_id, attachment_ids)
        if len(owned) != len(set(attachment_ids)):
            return error("Anexo inválido.", 403)
        marks = ",".join("?" for _ in owned)
        attachments = conn.all(f"SELECT id, kind FROM sg_assets WHERE id IN ({marks})", list(owned))

    ts = now()
    add_message(conv["id"], "user", texto, attachments)
    if conv["title"] in (NEW_TITLE, "Novo encarte") and texto:
        conn.run("UPDATE sg_conversations SET title = ? WHERE id = ?", (texto.splitlines()[0][:60], conv["id"]))

    options = data.get("opcoes") if isinstance(data.get("opcoes"), dict) else {}
    mode = conv.get("mode") or "ad"  # conversas antigas (antes da criação livre) eram encartes
    previous_result = last_result_asset(conv["id"])
    if mode != "ad" and creative.classify(texto, bool(previous_result)) != "ad_composite":
        return creative_message(conv, texto, attachments, options, previous_result)
    if mode != "ad":
        conn.run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))

    previous = loads(conv["briefing"]) or bf.normalize_briefing({})
    previous = apply_attachments(previous, attachments)
    assistant_id = new_id()

    if interpreter_available():
        history = conn.all(
            "SELECT role, content FROM sg_messages WHERE conversation_id = ? AND status = 'ok' ORDER BY seq DESC LIMIT 12",
            (conv["id"],),
        )[::-1]
        images = [a["id"] for a in attachments if a["kind"] in ("reference", "product", "logo")]
        payload = {
            "conversation_id": conv["id"],
            "assistant_message_id": assistant_id,
            "history": history,
            "previous_briefing": previous,
            "image_asset_ids": images,
        }
        add_message(conv["id"], "assistant", "Lendo o seu pedido…", status="pending", message_id=assistant_id)
        conn.run(
            "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, 'interpret', 'queued', ?, ?, ?)",
            (new_id(), g.user_id, conv["id"], dumps(payload), ts, ts),
        )
        save_briefing(conv["id"], previous)
    else:
        interpret_with_rules(conv["id"], texto, previous, assistant_id, insert=True)
    return jsonify({"ok": True}), 201


def interpret_with_rules(conversation_id, texto, previous, assistant_id, insert, note=""):
    updated = bf.interpret_rules(texto, previous)
    texts = user_texts(conversation_id) + confirmed_prices(previous)
    owned = owned_asset_ids(g.user_id, briefing_asset_ids(updated))
    normalized = bf.normalize_briefing(updated, texts, owned)
    save_briefing(conversation_id, normalized)
    summary = bf.assistant_summary(normalized, note)
    if insert:
        add_message(conversation_id, "assistant", summary, message_id=assistant_id)
    else:
        get_conn().run(
            "UPDATE sg_messages SET content = ?, status = 'ok' WHERE id = ?", (summary, assistant_id)
        )


def resolve_stale_interpretation(conv):
    """Se o worker HostGator não respondeu a tempo, interpreta por regras e avisa."""
    conn = get_conn()
    timeout = cfg().interpret_timeout_s
    stale = conn.all(
        "SELECT * FROM sg_jobs WHERE conversation_id = ? AND kind = 'interpret' AND "
        "((status = 'queued' AND created_at < ?) OR (status IN ('claimed', 'running') AND created_at < ?))",
        (conv["id"], now() - timeout, now() - 3 * timeout),
    )
    for job in stale:
        changed = conn.run(
            "UPDATE sg_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ? AND status = ?",
            ("tempo esgotado; usado modo regras", now(), job["id"], job["status"]),
        )
        if not changed:
            continue
        payload = loads(job["payload"], {})
        if payload.get("mode") == "creative":
            g.user_id = job["user_id"]
            current_app.logger.warning("interpretação %s sem resposta a tempo; usando regras", job["id"])
            creative_fallback(payload)
            continue
        last_user = next((m["content"] for m in reversed(payload.get("history") or []) if m["role"] == "user"), "")
        interpret_with_rules(
            conv["id"],
            last_user,
            payload.get("previous_briefing"),
            payload.get("assistant_message_id"),
            insert=False,
            note="Confira os dados do encarte no painel ao lado.",
        )


# ---------------------------------------------------------------- linguagem do cliente e estimativas

FRIENDLY_FAILURE = "Não consegui concluir sua imagem desta vez. Vamos tentar novamente?"
STAGES = ("queue", "load", "prep", "download", "upload", "encode", "sampling", "vae", "save", "compose", "send")


def clean_progress_info(data):
    stage = data.get("etapa")
    if stage not in STAGES:
        return {}
    info = {"etapa": stage}
    for key, top in (("passo", 1000), ("total", 1000), ("restante_s", 86400)):
        value = data.get(key)
        if isinstance(value, (int, float)) and 0 <= value <= top:
            info[key] = int(value)
    return info


def friendly_status(job, estimate=None):
    """Mensagem natural para o cliente, derivada do estado REAL (etapa e passo informados pelo agente)."""
    status = job["status"]
    if status == "done":
        return "Pronto! Sua criação está disponível."
    if status == "failed":
        return FRIENDLY_FAILURE
    if status == "canceled":
        return "Criação cancelada."
    if status == "queued":
        waiting = now() - job["created_at"]
        if waiting > 90:
            return "Muitas pessoas criando agora. Sua imagem começa logo em seguida."
        return "Sua imagem vai começar em instantes…"
    info = loads(job.get("progress_info"), {}) or {}
    stage = info.get("etapa")
    if job.get("gen_type") == "ad_composite" and stage in ("compose", "send"):
        return "Montando seu encarte com os produtos e preços…"
    if stage == "sampling" and info.get("total"):
        message = ("Estou criando os detalhes da sua imagem…" if info.get("passo", 0) < info["total"] / 2
                   else "Ajustando iluminação, cores e composição…")
    elif stage in ("vae", "save", "send", "compose"):
        message = "Sua imagem está quase pronta!"
    elif stage == "encode":
        message = "Entendendo os detalhes do seu pedido…"
    elif status == "claimed" or stage in ("queue", "load", "prep", "download", "upload", None):
        message = "Entendi sua ideia! Preparando tudo para criar sua imagem…"
    else:
        message = "Estou criando a sua imagem…"
    started = job.get("started_at")
    if estimate and started and now() - started > estimate * 1.4 + 30:
        message = "Está levando um pouco mais que o normal, mas continuo trabalhando na sua imagem…"
    return message


def remaining_seconds(job, estimate, elapsed):
    if job["status"] not in ("claimed", "running"):
        return None
    info = loads(job.get("progress_info"), {}) or {}
    if isinstance(info.get("restante_s"), int):
        return info["restante_s"]
    if estimate and elapsed is not None:
        return max(0, estimate - elapsed)
    return None


def images(n):
    return f"{n} imagem" if n == 1 else f"{n} imagens"


MIN_SAMPLES = 3


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


def estimate_for_job(job, payload):
    """Tempo típico (início ao fim) de gerações parecidas: mesmo tamanho, passos e tipo de pedido.

    Só devolve valor com pelo menos MIN_SAMPLES medições reais; sem confiança, nada é mostrado.
    """
    if job["kind"] != "render" or job["status"] in ("done", "failed", "canceled"):
        return None
    size = payload.get("size") or payload.get("scene_size")
    if not size:
        return None
    cache = g.setdefault("sg_estimates", {})
    key = (tuple(size), payload.get("steps"), payload.get("render_mode") or "txt2img", job.get("gen_type"))
    if key not in cache:
        rows = get_conn().all(
            "SELECT payload, render_mode, gen_type, started_at, finished_at, timings FROM sg_jobs WHERE kind = 'render' "
            "AND status = 'done' AND started_at IS NOT NULL AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 200"
        )
        similar = []
        for row in rows:
            p = loads(row["payload"], {}) or {}
            if (tuple(p.get("size") or p.get("scene_size") or ()) == tuple(size) and p.get("steps") == payload.get("steps")
                    and (row["render_mode"] or "txt2img") == key[2] and row["gen_type"] == job.get("gen_type")):
                timings = loads(row["timings"], {}) or {}
                # Gerações com o pedido já em memória (outra versão) são bem mais rápidas: não entram na estimativa de pedido novo.
                if timings.get("encode_s", 1.0) >= 1.0 or "encode_s" not in timings:
                    similar.append(row["finished_at"] - row["started_at"])
            if len(similar) >= 20:
                break
        cache[key] = _median(similar) if len(similar) >= MIN_SAMPLES else None
    return cache[key]


# ---------------------------------------------------------------- criação livre (free_image)

NEW_TITLE = "Nova criação"


def last_result_asset(conversation_id):
    row = get_conn().one(
        "SELECT result FROM sg_jobs WHERE conversation_id = ? AND kind = 'render' AND status = 'done' "
        "AND gen_type = 'free_image' ORDER BY finished_at DESC LIMIT 1",
        (conversation_id,),
    )
    return (loads(row["result"], {}) or {}).get("asset_id") if row else None


BRT_OFFSET = -3 * 3600  # Brasil sem horário de verão desde 2019


def day_start(ts=None):
    ts = now() if ts is None else ts
    local = ts + BRT_OFFSET
    return local - local % 86400 - BRT_OFFSET


def month_start(ts=None):
    import datetime

    utc = datetime.timezone.utc
    local = datetime.datetime.fromtimestamp((now() if ts is None else ts) + BRT_OFFSET, utc)
    return int(datetime.datetime(local.year, local.month, 1, tzinfo=utc).timestamp()) - BRT_OFFSET


COUNTED = "kind = 'render' AND status NOT IN ('failed', 'canceled')"


def user_limits(user_id):
    row = get_conn().one("SELECT daily_limit, monthly_limit FROM sg_users WHERE id = ?", (user_id,)) or {}
    daily = row.get("daily_limit")
    return (cfg().daily_job_quota if daily is None else daily), row.get("monthly_limit")


def user_usage(user_id):
    conn = get_conn()
    row = conn.one(
        f"SELECT SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS hoje, "
        f"SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS mes, COUNT(*) AS total FROM sg_jobs WHERE user_id = ? AND {COUNTED}",
        (day_start(), month_start(), user_id),
    ) or {}
    daily, monthly = user_limits(user_id)
    return {"hoje": int(row.get("hoje") or 0), "mes": int(row.get("mes") or 0), "total": int(row.get("total") or 0),
            "limite_diario": daily, "limite_mensal": monthly}


def render_quota_problem(user_id):
    conn = get_conn()
    usage = user_usage(user_id)
    if usage["hoje"] >= usage["limite_diario"]:
        return (f"Você atingiu o limite de {images(usage['limite_diario'])} por dia. Amanhã ele renova; "
                "se precisar de mais, fale com o administrador.")
    if usage["limite_mensal"] is not None and usage["mes"] >= usage["limite_mensal"]:
        return (f"Você atingiu o limite de {images(usage['limite_mensal'])} neste mês. "
                "Se precisar de mais, fale com o administrador.")
    active = conn.one(
        "SELECT COUNT(*) AS n FROM sg_jobs WHERE user_id = ? AND kind = 'render' AND status IN ('queued', 'claimed', 'running')",
        (user_id,),
    )["n"]
    if active >= 2:
        return "Você já tem imagens sendo criadas. Assim que uma terminar, posso começar a próxima."
    return None


def enqueue_free_image(conv, user_id, spec):
    """Cria o job v2 (free_image). Referências e imagem base precisam ser do próprio usuário."""
    problem = render_quota_problem(user_id)
    if problem:
        return None, problem
    spec = dict(spec)
    owned = owned_asset_ids(user_id, [spec.get("init_asset_id"), *(spec.get("reference_ids") or [])])
    spec["reference_ids"] = [r for r in spec.get("reference_ids") or [] if r in owned]
    if spec.get("init_asset_id") not in owned:
        spec.update(init_asset_id=None, render_mode="txt2img", denoise=1.0)
    payload = {
        "version": 2,
        "gen_type": "free_image",
        "prompt": spec["prompt"],
        "negative_prompt": spec["negative_prompt"],
        "seed": spec["seed"],
        "size": spec["size"],
        "steps": spec.get("steps"),
        "render_mode": spec["render_mode"],
        "init_asset_id": spec.get("init_asset_id"),
        "denoise": spec["denoise"],
        "asset_ids": [spec["init_asset_id"]] if spec.get("init_asset_id") else [],
        "spec": spec,
    }
    job_id, ts = new_id(), now()
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, gen_type, "
        "prompt_original, prompt_visual, negative_prompt, style, aspect_ratio, seed, reference_ids, render_mode, "
        "parent_job_id, created_at, updated_at) VALUES (?, ?, ?, 'render', 'queued', ?, ?, 2, 'free_image', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload), "Na fila do gerador", spec["prompt_original"], spec["prompt_visual"],
         spec["negative_prompt"], spec["style"], spec["aspect_ratio"], spec["seed"], dumps(spec["reference_ids"]),
         spec["render_mode"], spec.get("parent_job_id"), ts, ts),
    )
    save_spec(conv["id"], {**spec, "job_id": job_id})
    return job_id, None


def save_spec(conversation_id, spec):
    get_conn().run("UPDATE sg_conversations SET spec = ?, updated_at = ? WHERE id = ?", (dumps(spec), now(), conversation_id))


def conversation_texts(conversation_id):
    return user_texts(conversation_id)[-12:]


def apply_creative_decision(conv, acao, resposta, spec, message_id, insert, texto):
    """Executa a decisão (gerar | perguntar | encarte) e escreve a resposta do assistente."""
    job_id = None
    if acao == "encarte":
        get_conn().run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))
        previous = loads(conv["briefing"]) or bf.normalize_briefing({})
        interpret_with_rules(conv["id"], texto, previous, message_id, insert=insert,
                             note="Entendi que é um encarte com produtos e preços: preencha e confira o briefing ao lado.")
        return None
    if acao == "perguntar":
        save_spec(conv["id"], spec)
    else:
        job_id, problem = enqueue_free_image(conv, g.user_id, spec)
        if problem:
            resposta = problem
    if insert:
        add_message(conv["id"], "assistant", resposta, message_id=message_id, job_id=job_id)
    else:
        set_message(message_id, conv["id"], resposta, job_id)
    return job_id


def creative_message(conv, texto, attachments, options, previous_result):
    conn = get_conn()
    previous_spec = loads(conv.get("spec"), None)
    references = [a["id"] for a in attachments if a["kind"] in ("reference", "product", "logo")]
    assistant_id = new_id()
    if interpreter_available():
        history = conn.all(
            "SELECT role, content FROM sg_messages WHERE conversation_id = ? AND status = 'ok' ORDER BY seq DESC LIMIT 10",
            (conv["id"],),
        )[::-1]
        payload = {
            "mode": "creative",
            "conversation_id": conv["id"],
            "assistant_message_id": assistant_id,
            "history": history,
            "text": texto,
            "options": options,
            "previous_spec": previous_spec,
            "previous_result_id": previous_result,
            "image_asset_ids": references[:3],
            "styles": list(creative.STYLES),
            "aspects": list(creative.ASPECTS),
        }
        add_message(conv["id"], "assistant", "Pensando na sua ideia…", status="pending", message_id=assistant_id)
        conn.run(
            "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, 'interpret', 'queued', ?, ?, ?)",
            (new_id(), g.user_id, conv["id"], dumps(payload), now(), now()),
        )
        return jsonify({"ok": True}), 201
    acao, resposta, spec = creative.rules_interpretation(
        texto, options, previous_spec, previous_result, references, None, conversation_texts(conv["id"])
    )
    apply_creative_decision(conv, acao, resposta, spec, assistant_id, True, texto)
    return jsonify({"ok": True}), 201


def _creative_context(payload):
    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
    references = list(owned_asset_ids(conv["user_id"], payload.get("image_asset_ids") or []))
    return conv, references


def creative_from_model(payload, model_answer):
    conv, references = _creative_context(payload)
    acao, resposta, spec = creative.normalize_model_answer(
        model_answer, payload.get("text", ""), payload.get("options"), payload.get("previous_spec"),
        payload.get("previous_result_id"), references, conversation_texts(conv["id"]),
    )
    apply_creative_decision(conv, acao, resposta, spec, payload["assistant_message_id"], False, payload.get("text", ""))


def creative_fallback(payload, note=""):
    conv, references = _creative_context(payload)
    acao, resposta, spec = creative.rules_interpretation(
        payload.get("text", ""), payload.get("options"), payload.get("previous_spec"),
        payload.get("previous_result_id"), references, None, conversation_texts(conv["id"]),
    )
    apply_creative_decision(conv, acao, f"{resposta} {note}".strip(), spec, payload["assistant_message_id"], False,
                            payload.get("text", ""))


TIMING_KEYS = ("download_s", "upload_ref_s", "queue_comfy_s", "comfy_s", "fetch_s", "compose_s", "total_worker_s",
               "vram_livre_mb", "ram_livre_mb", "load_s", "prep_s", "encode_s", "sampling_s", "vae_s", "steps",
               "s_por_passo", "modelo_em_cache", "preparo_amostragem_s", "vram_min_encode_mb", "vram_min_sampling_mb",
               "vram_min_vae_mb", "ram_min_mb")


def clean_timings(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in TIMING_KEYS:
        v = value.get(key)
        if isinstance(v, (int, float)) and 0 <= v < 10**7:
            out[key] = round(float(v), 2)
    return out


def touch_worker(worker_id, kind):
    conn, ts = get_conn(), now()
    if not conn.run("UPDATE sg_worker_status SET last_seen = ?, kind = ? WHERE worker_id = ?", (ts, kind, worker_id)):
        conn.run("INSERT INTO sg_worker_status (worker_id, kind, last_seen) VALUES (?, ?, ?)", (worker_id, kind, ts))


@bp.put("/api/conversations/<conversation_id>/briefing")
@login_required
def api_update_briefing(conversation_id):
    blocked = limit("brief", 60, 60)
    if blocked:
        return blocked
    conv = get_conversation(conversation_id)
    data = (request.get_json(silent=True) or {}).get("briefing")
    owned = owned_asset_ids(g.user_id, briefing_asset_ids(data))
    # Preços digitados no formulário de confirmação são do próprio usuário.
    normalized = bf.normalize_briefing(data, owned_asset_ids=owned, trust_prices=True)
    save_briefing(conv["id"], normalized)
    ok, motivo = bf.ready_for_render(normalized)
    return jsonify({"ok": True, "briefing": normalized, "pode_gerar": ok, "motivo": motivo})


@bp.post("/api/uploads")
@login_required
def api_upload():
    blocked = limit("upload", 30, 600)
    if blocked:
        return blocked
    kind = request.form.get("tipo", "reference")
    if kind not in ASSET_KINDS:
        return error("Tipo de anexo inválido.")
    file = request.files.get("arquivo")
    if not file:
        return error("Nenhum arquivo enviado.")
    raw = file.read(cfg().max_upload_bytes + 1)
    try:
        data, mime, width, height = sanitize_image(
            raw, cfg().max_upload_bytes, cfg().max_image_pixels, cfg().max_image_side
        )
    except ImageRejected as exc:
        return error(str(exc))
    asset_id = store_asset(g.user_id, kind, data, mime, width, height)
    return jsonify({"ok": True, "asset": {"id": asset_id, "kind": kind, "largura": width, "altura": height}}), 201


def store_asset(user_id, kind, data, mime, width, height, source_url=None, license=None, attribution=None):
    asset_id = new_id()
    conn = get_conn()
    storage_key = None
    if storage.enabled():
        try:
            storage_key = storage.put(asset_id, data, mime)
        except Exception as exc:  # noqa: BLE001 — sem Cloudinary, a imagem fica no banco
            current_app.logger.warning("Cloudinary indisponível (%s); imagem salva no banco", type(exc).__name__)
    conn.run(
        "INSERT INTO sg_assets (id, user_id, kind, mime, size, width, height, sha256, source_url, license, attribution, data, "
        "storage_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            user_id,
            kind,
            mime,
            len(data),
            width,
            height,
            hashlib.sha256(data).hexdigest(),
            source_url,
            license,
            attribution,
            None if storage_key else conn.blob(data),
            storage_key,
            now(),
        ),
    )
    return asset_id


def asset_bytes(asset):
    if asset.get("storage_key"):
        return storage.get(asset["storage_key"], asset["mime"])
    return asset["data"]


def asset_response(asset, download=False):
    resp = Response(asset_bytes(asset), mimetype=asset["mime"])
    ext = "png" if asset["mime"] == "image/png" else "jpg"
    disposition = "attachment" if download else "inline"
    resp.headers["Content-Disposition"] = f'{disposition}; filename="sitegenius-{asset["id"][:8]}.{ext}"'
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.get("/api/assets/<asset_id>")
@login_required
def api_asset(asset_id):
    asset = get_conn().one(
        "SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (valid_id(asset_id), g.user_id)
    )
    if not asset:
        abort(404)
    return asset_response(asset, download=request.args.get("download") == "1")


@bp.get("/api/products/search")
@login_required
def api_product_search():
    blocked = limit("search", 30, 60)
    if blocked:
        return blocked
    ean = request.args.get("ean", "")
    query = request.args.get("q", "")
    results = lookup_ean(ean) if ean else search_name(query)
    return jsonify({"ok": True, "resultados": results})


@bp.post("/api/products/import")
@login_required
def api_product_import():
    blocked = limit("import", 30, 600)
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    url = str(data.get("image_url") or "")
    try:
        raw = safe_download(url, cfg().max_upload_bytes * 2)
        image, mime, width, height = sanitize_image(
            raw, cfg().max_upload_bytes * 2, cfg().max_image_pixels, cfg().max_image_side
        )
    except (UnsafeUrl, ImageRejected) as exc:
        current_app.logger.info("foto de produto recusada: %s", exc)
        return error("Não foi possível usar esta foto. Escolha outra ou envie uma foto sua.")
    license = bf._clean_text(data.get("license"), 40) or "CC BY-SA 3.0"
    attribution = bf._clean_text(data.get("attribution"), 80) or "Open Food Facts contributors"
    asset_id = store_asset(g.user_id, "product", image, mime, width, height, url, license, attribution)
    return jsonify({"ok": True, "asset": {"id": asset_id, "kind": "product"}}), 201


@bp.post("/api/conversations/<conversation_id>/jobs")
@login_required
def api_create_job(conversation_id):
    blocked = limit("job", 6, 600)
    if blocked:
        return blocked
    conv = get_conversation(conversation_id)
    briefing = loads(conv["briefing"]) or {}
    owned = owned_asset_ids(g.user_id, briefing_asset_ids(briefing))
    briefing = bf.normalize_briefing(briefing, owned_asset_ids=owned, trust_prices=True)
    ok, motivo = bf.ready_for_render(briefing)
    if not ok:
        return error(motivo)
    conn = get_conn()
    problem = render_quota_problem(g.user_id)
    if problem:
        return error(problem, 429)

    asset_ids = sorted(owned)
    licenses = []
    if asset_ids:
        marks = ",".join("?" for _ in asset_ids)
        for row in conn.all(f"SELECT license, attribution FROM sg_assets WHERE id IN ({marks}) AND license IS NOT NULL", asset_ids):
            licenses.append(f"{row['attribution']} ({row['license']})")
    width, height = SCENE_SIZES[briefing["formato"]]
    payload = {
        "version": 2,
        "gen_type": "ad_composite",
        "briefing": briefing,
        "scene_prompt": bf.scene_prompt(briefing),
        "scene_size": [width, height],
        "steps": creative.steps_for("qualidade"),
        "asset_ids": asset_ids,
        "licenses": licenses,
    }
    job_id, ts = new_id(), now()
    conn.run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, "
        "gen_type, prompt_visual, aspect_ratio, render_mode, created_at, updated_at) "
        "VALUES (?, ?, ?, 'render', 'queued', ?, ?, 2, 'ad_composite', ?, ?, 'txt2img', ?, ?)",
        (job_id, g.user_id, conv["id"], dumps(payload), "Na fila do gerador", payload["scene_prompt"][:1200],
         briefing["formato"], ts, ts),
    )
    return jsonify({"ok": True, "job": job_view(conn.one("SELECT * FROM sg_jobs WHERE id = ?", (job_id,)))}), 201


@bp.get("/api/jobs/<job_id>")
@login_required
def api_job(job_id):
    job = get_conn().one("SELECT * FROM sg_jobs WHERE id = ? AND user_id = ?", (valid_id(job_id), g.user_id))
    if not job:
        abort(404)
    return jsonify({"ok": True, "job": job_view(job)})


@bp.post("/api/jobs/<job_id>/regenerate")
@login_required
def api_regenerate(job_id):
    """Outra versão da mesma criação (nova seed). Encartes usam o botão "Gerar encarte"."""
    blocked = limit("job", 6, 600)
    if blocked:
        return blocked
    job = get_conn().one("SELECT * FROM sg_jobs WHERE id = ? AND user_id = ?", (valid_id(job_id), g.user_id))
    if not job or job.get("gen_type") != "free_image":
        abort(404)
    spec = dict((loads(job["payload"], {}) or {}).get("spec") or {})
    if not spec:
        return error("Esta geração não pode ser refeita.")
    spec["seed"] = creative.build_spec(prompt_original="", prompt_visual="x", style="auto", aspect=None,
                                       quality=None, user_texts=[])["seed"]
    spec["parent_job_id"] = job["id"]
    conv = get_conversation(job["conversation_id"])
    new_job_id, problem = enqueue_free_image(conv, g.user_id, spec)
    if problem:
        return error(problem, 429)
    add_message(conv["id"], "assistant", "Vou criar outra versão para você.", job_id=new_job_id)
    return jsonify({"ok": True, "job": job_view(get_conn().one("SELECT * FROM sg_jobs WHERE id = ?", (new_job_id,)))}), 201


@bp.post("/api/jobs/<job_id>/cancel")
@login_required
def api_cancel_job(job_id):
    changed = get_conn().run(
        "UPDATE sg_jobs SET status = 'canceled', status_message = 'Cancelado', updated_at = ? "
        "WHERE id = ? AND user_id = ? AND status IN ('queued', 'claimed', 'running')",
        (now(), valid_id(job_id), g.user_id),
    )
    if not changed:
        return error("Esta geração não pode mais ser cancelada.", 409)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- administração (contas, limites, uso)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not (g.get("sg_user") or {}).get("is_admin"):
            if request.path.startswith("/studio/api"):
                return error("Acesso restrito ao administrador.", 403)
            return redirect("/studio/", 302)
        return view(*args, **kwargs)

    return wrapper


@bp.get("/admin")
@admin_required
def admin_page():
    return render_page("studio/admin.html", csrf_token=csrf_token())


def admin_user_view(row, usage):
    daily = row.get("daily_limit")
    return {
        "id": row["id"], "nome": row["name"], "email": row["email"], "ativo": bool(row["active"]),
        "admin": bool(row.get("is_admin")), "trocar_senha": bool(row.get("must_change_password")),
        "limite_diario": daily, "limite_diario_efetivo": cfg().daily_job_quota if daily is None else daily,
        "limite_mensal": row.get("monthly_limit"), "observacoes": row.get("notes") or "",
        "criado_em": row.get("created_at"), "ultimo_login": row.get("last_login_at"),
        "imagens_hoje": int(usage.get("hoje") or 0), "imagens_mes": int(usage.get("mes") or 0),
        "imagens_total": int(usage.get("total") or 0), "ultima_imagem": usage.get("ultima"),
        "falhas_mes": int(usage.get("falhas") or 0),
    }


def usage_by_user():
    rows = get_conn().all(
        "SELECT user_id, "
        "SUM(CASE WHEN status = 'done' AND created_at >= ? THEN 1 ELSE 0 END) AS hoje, "
        "SUM(CASE WHEN status = 'done' AND created_at >= ? THEN 1 ELSE 0 END) AS mes, "
        "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS total, "
        "SUM(CASE WHEN status = 'failed' AND created_at >= ? THEN 1 ELSE 0 END) AS falhas, "
        "MAX(CASE WHEN status = 'done' THEN finished_at END) AS ultima "
        "FROM sg_jobs WHERE kind = 'render' GROUP BY user_id",
        (day_start(), month_start(), month_start()),
    )
    return {r["user_id"]: r for r in rows}


@bp.get("/api/admin/users")
@admin_required
def admin_users():
    rows = get_conn().all(
        "SELECT id, email, name, active, is_admin, daily_limit, monthly_limit, must_change_password, created_at, "
        "last_login_at, notes FROM sg_users ORDER BY active DESC, name"
    )
    usage = usage_by_user()
    return jsonify({"ok": True, "usuarios": [admin_user_view(r, usage.get(r["id"], {})) for r in rows],
                    "limite_diario_padrao": cfg().daily_job_quota})


@bp.get("/api/admin/stats")
@admin_required
def admin_stats():
    conn = get_conn()
    users = conn.one("SELECT COUNT(*) AS total, SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS ativos FROM sg_users")
    images = conn.one(
        "SELECT SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS hoje, SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS mes, "
        "COUNT(*) AS total, AVG(CASE WHEN created_at >= ? THEN finished_at - started_at END) AS media_geracao_s "
        "FROM sg_jobs WHERE kind = 'render' AND status = 'done'",
        (day_start(), month_start(), month_start()),
    )
    queue = conn.one("SELECT COUNT(*) AS n FROM sg_jobs WHERE kind = 'render' AND status IN ('queued', 'claimed', 'running')")
    days = []
    start = day_start() - 13 * 86400
    rows = conn.all(
        "SELECT created_at FROM sg_jobs WHERE kind = 'render' AND status = 'done' AND created_at >= ?", (start,)
    )
    counts = [0] * 14
    for r in rows:
        counts[min(13, (r["created_at"] - start) // 86400)] += 1
    for i, n in enumerate(counts):
        days.append({"dia": start + i * 86400, "imagens": n})
    return jsonify({
        "ok": True,
        "usuarios_total": int(users["total"] or 0), "usuarios_ativos": int(users["ativos"] or 0),
        "imagens_hoje": int(images["hoje"] or 0), "imagens_mes": int(images["mes"] or 0),
        "imagens_total": int(images["total"] or 0),
        "media_geracao_s": round(images["media_geracao_s"]) if images["media_geracao_s"] else None,
        "fila": int(queue["n"] or 0), "gerador_online": workers_online("render"),
        "interpretacao": "ia" if interpreter_available() else "regras", "ultimos_14_dias": days,
    })


@bp.get("/api/admin/jobs")
@admin_required
def admin_jobs():
    """Atividade recente com detalhes técnicos (só no painel do administrador)."""
    rows = get_conn().all(
        "SELECT j.id, j.kind, j.gen_type, j.status, j.status_message, j.error, j.payload, j.timings, j.worker_id, "
        "j.render_mode, j.created_at, j.started_at, j.finished_at, u.name AS user_name "
        "FROM sg_jobs j LEFT JOIN sg_users u ON u.id = j.user_id ORDER BY j.created_at DESC LIMIT 40"
    )
    out = []
    for r in rows:
        payload = loads(r["payload"], {}) or {}
        out.append({
            "id": r["id"][:8], "usuario": r["user_name"] or "—", "tipo": r["gen_type"] or ("interpretação" if r["kind"] == "interpret" else r["kind"]),
            "status": r["status"], "detalhe": r["error"] or r["status_message"] or "", "worker": r["worker_id"] or "",
            "tamanho": payload.get("size") or payload.get("scene_size"), "passos": payload.get("steps"),
            "modo": r["render_mode"] or "", "tempos": loads(r["timings"], {}) or {},
            "fila_s": (r["started_at"] - r["created_at"]) if r["started_at"] else None,
            "geracao_s": (r["finished_at"] - r["started_at"]) if r["finished_at"] and r["started_at"] else None,
            "criado_em": r["created_at"],
        })
    return jsonify({"ok": True, "jobs": out})


@bp.post("/api/admin/users")
@admin_required
def admin_create_user():
    data = json_body()
    temp = accounts.generate_temp_password()
    try:
        user = accounts.create_user(
            get_conn(), data.get("nome"), data.get("email"), temp, is_admin=data.get("admin") is True,
            daily_limit=data.get("limite_diario"), monthly_limit=data.get("limite_mensal"), must_change=True,
            created_by=g.user_id, notes=data.get("observacoes"),
        )
    except accounts.AccountError as exc:
        return error(str(exc))
    current_app.logger.info("studio admin %s criou o usuário %s", g.user_id, user["id"])
    # A senha provisória só aparece nesta resposta (não é guardada em texto).
    return jsonify({"ok": True, "id": user["id"], "senha_provisoria": temp}), 201


@bp.patch("/api/admin/users/<int:user_id>")
@admin_required
def admin_update_user(user_id):
    data = json_body()
    conn = get_conn()
    if not conn.one("SELECT id FROM sg_users WHERE id = ?", (user_id,)):
        abort(404)
    updates = {}
    try:
        if "nome" in data:
            name = " ".join(str(data["nome"] or "").split())[:120]
            if len(name) < 2:
                raise accounts.AccountError("Informe o nome.")
            updates["name"] = name
        if "limite_diario" in data:
            updates["daily_limit"] = accounts.clean_limit(data["limite_diario"])
        if "limite_mensal" in data:
            updates["monthly_limit"] = accounts.clean_limit(data["limite_mensal"])
        if "observacoes" in data:
            updates["notes"] = " ".join(str(data["observacoes"] or "").split())[:300]
        if "ativo" in data:
            if user_id == g.user_id and data["ativo"] is not True:
                raise accounts.AccountError("Você não pode desativar a sua própria conta.")
            updates["active"] = 1 if data["ativo"] is True else 0
        if "admin" in data:
            if user_id == g.user_id and data["admin"] is not True:
                raise accounts.AccountError("Você não pode remover o seu próprio acesso de administrador.")
            updates["is_admin"] = 1 if data["admin"] is True else 0
    except accounts.AccountError as exc:
        return error(str(exc))
    if not updates:
        return error("Nada para alterar.")
    sets = ", ".join(f"{column} = ?" for column in updates)
    extra = ", session_version = session_version + 1" if updates.get("active") == 0 else ""  # derruba as sessões
    conn.run(f"UPDATE sg_users SET {sets}{extra}, updated_at = ? WHERE id = ?", [*updates.values(), now(), user_id])
    return jsonify({"ok": True})


@bp.post("/api/admin/users/<int:user_id>/reset-password")
@admin_required
def admin_reset_password(user_id):
    try:
        temp = accounts.admin_reset_password(get_conn(), user_id)
    except accounts.AccountError as exc:
        return error(str(exc), 404)
    if user_id == g.user_id:
        end_session()
    return jsonify({"ok": True, "senha_provisoria": temp})


# ---------------------------------------------------------------- API dos workers (HMAC)


def worker_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        body = request.get_data(cache=True)
        worker_id, worker = verify_worker_request(
            get_conn(), cfg().workers, request.headers, request.method, request.path, body
        )
        if not worker_id:
            current_app.logger.warning("studio worker rejeitado: %s", worker)
            return error("não autorizado", 401)
        g.worker_id, g.worker = worker_id, worker
        touch_worker(worker_id, worker["kind"])
        return view(*args, **kwargs)

    return wrapper


def worker_job(job_id):
    job = get_conn().one(
        "SELECT * FROM sg_jobs WHERE id = ? AND worker_id = ? AND status IN ('claimed', 'running')",
        (valid_id(job_id), g.worker_id),
    )
    if not job:
        abort(409)
    return job


@bp.post("/worker/claim")
@worker_required
def worker_claim():
    conn = get_conn()
    kind, ts = g.worker["kind"], now()
    conn.run(
        "UPDATE sg_jobs SET status = 'failed', error = 'excedeu tentativas', updated_at = ? "
        "WHERE kind = ? AND status IN ('claimed', 'running') AND lease_until < ? AND attempts >= ?",
        (ts, kind, ts, cfg().max_attempts),
    )
    conn.run(
        "UPDATE sg_jobs SET status = 'queued', worker_id = NULL, updated_at = ? "
        "WHERE kind = ? AND status IN ('claimed', 'running') AND lease_until < ?",
        (ts, kind, ts),
    )
    candidates = conn.all(
        "SELECT id FROM sg_jobs WHERE kind = ? AND status = 'queued' ORDER BY created_at LIMIT 5", (kind,)
    )
    for candidate in candidates:
        claimed = conn.run(
            "UPDATE sg_jobs SET status = 'claimed', worker_id = ?, attempts = attempts + 1, lease_until = ?, "
            "status_message = ?, started_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
            (g.worker_id, ts + cfg().job_lease_s, "Recebido pelo gerador" if kind == "render" else "Analisando",
             ts, ts, candidate["id"]),
        )
        if claimed:
            conn.commit()
            job = conn.one("SELECT id, kind, payload FROM sg_jobs WHERE id = ?", (candidate["id"],))
            return jsonify({"ok": True, "job": {"id": job["id"], "kind": job["kind"], "payload": loads(job["payload"])}})
    return "", 204


@bp.post("/worker/jobs/<job_id>/progress")
@worker_required
def worker_progress(job_id):
    job = worker_job(job_id)
    data = request.get_json(silent=True) or {}
    progress = max(0, min(99, int(data.get("progress") or 0)))
    message = bf._clean_text(data.get("mensagem"), 200)
    get_conn().run(
        "UPDATE sg_jobs SET status = 'running', progress = ?, status_message = ?, lease_until = ?, updated_at = ? WHERE id = ?",
        (progress, message, now() + cfg().job_lease_s, now(), job["id"]),
    )
    timings = clean_timings(data.get("tempos"))
    if timings:
        get_conn().run("UPDATE sg_jobs SET timings = ? WHERE id = ?", (dumps(timings), job["id"]))
    info = clean_progress_info(data)
    if info:
        get_conn().run("UPDATE sg_jobs SET progress_info = ? WHERE id = ?", (dumps(info), job["id"]))
    return jsonify({"ok": True})


@bp.post("/worker/benchmarks")
@worker_required
def worker_benchmarks():
    """Recebe o resultado do --bench do Acer (só workers de render). Valores validados e limitados."""
    if g.worker["kind"] != "render":
        return error("não autorizado", 403)
    rows = (request.get_json(silent=True) or {}).get("linhas")
    if not isinstance(rows, list):
        return error("formato inválido")
    conn, accepted = get_conn(), 0

    def num(value, top=100000):
        return round(float(value), 2) if isinstance(value, (int, float)) and 0 <= value < top else None

    for row in rows[:60]:
        if not isinstance(row, dict):
            continue
        size = str(row.get("tamanho") or "")
        if not re.fullmatch(r"\d{2,4}x\d{2,4}", size):
            continue
        steps = row.get("passos") if isinstance(row.get("passos"), int) and 0 < row.get("passos") <= 50 else None
        run = row.get("execucao") if isinstance(row.get("execucao"), int) else None
        load = num(row.get("load_s"))
        cold = 1 if (row.get("modelo_em_cache") == 0 or (load is not None and load >= 1.0)) else 0
        conn.run(
            "INSERT INTO sg_benchmarks (id, worker_id, size, steps, run, comfy_s, load_s, encode_s, sampling_s, vae_s, cold, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), g.worker_id, size, steps, run, num(row.get("comfy_s")), load, num(row.get("encode_s")),
             num(row.get("sampling_s")), num(row.get("vae_s")), cold, now()),
        )
        accepted += 1
    return jsonify({"ok": True, "aceitas": accepted})


@bp.get("/worker/assets/<asset_id>")
@worker_required
def worker_asset(asset_id):
    asset_id = valid_id(asset_id)
    jobs = get_conn().all(
        "SELECT payload, user_id FROM sg_jobs WHERE worker_id = ? AND status IN ('claimed', 'running')", (g.worker_id,)
    )
    for job in jobs:
        payload = loads(job["payload"], {})
        allowed = set(payload.get("asset_ids") or []) | set(payload.get("image_asset_ids") or [])
        if asset_id in allowed:
            asset = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (asset_id, job["user_id"]))
            if asset:
                return asset_response(asset)
    abort(404)


@bp.post("/worker/jobs/<job_id>/complete")
@worker_required
def worker_complete(job_id):
    job = worker_job(job_id)
    conn = get_conn()
    if job["kind"] == "render":
        try:
            data, mime, width, height = sanitize_image(request.get_data(), 8 * 1024 * 1024, 40_000_000, 4096)
        except ImageRejected as exc:
            return error(str(exc))
        asset_id = store_asset(job["user_id"], "result", data, mime, width, height)
        conn.run(
            "UPDATE sg_jobs SET status = 'done', progress = 100, status_message = 'Pronto', result = ?, finished_at = ?, "
            "updated_at = ? WHERE id = ?",
            (dumps({"asset_id": asset_id}), now(), now(), job["id"]),
        )
        return jsonify({"ok": True, "asset_id": asset_id})

    # Interpretação: nunca confiar no modelo, tudo passa pela validação.
    data = request.get_json(silent=True) or {}
    payload = loads(job["payload"], {})
    if payload.get("mode") == "creative":
        g.user_id = job["user_id"]
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ? WHERE id = ?",
                 (now(), now(), job["id"]))
        creative_from_model(payload, data.get("modelo"))
        return jsonify({"ok": True})
    conversation_id = payload["conversation_id"]
    previous = payload.get("previous_briefing") or {}
    proposed = merge_previous_assets(data.get("briefing") if isinstance(data.get("briefing"), dict) else {}, previous)
    texts = user_texts(conversation_id) + confirmed_prices(previous)
    ids = briefing_asset_ids(proposed)
    owned = set()
    if ids:
        marks = ",".join("?" for _ in ids)
        owned = {r["id"] for r in conn.all(f"SELECT id FROM sg_assets WHERE user_id = ? AND id IN ({marks})", [job["user_id"], *ids])}
    normalized = bf.normalize_briefing(proposed, texts, owned)
    resposta = bf._clean_text(data.get("resposta"), 1500) or "Atualizei o briefing."
    if normalized["pendencias"]:
        resposta += "\n\nFalta:\n" + "\n".join(f"– {p}" for p in normalized["pendencias"][:6])
    save_briefing(conversation_id, normalized)
    conn.run(
        "UPDATE sg_messages SET content = ?, status = 'ok' WHERE id = ? AND conversation_id = ?",
        (resposta, payload["assistant_message_id"], conversation_id),
    )
    conn.run(
        "UPDATE sg_jobs SET status = 'done', progress = 100, updated_at = ? WHERE id = ?", (now(), job["id"])
    )
    return jsonify({"ok": True})


@bp.post("/worker/jobs/<job_id>/fail")
@worker_required
def worker_fail(job_id):
    job = worker_job(job_id)
    data = request.get_json(silent=True) or {}
    message = bf._clean_text(data.get("erro"), 300) or "falha no worker"
    get_conn().run(
        "UPDATE sg_jobs SET status = 'failed', error = ?, status_message = 'Falhou', updated_at = ? WHERE id = ?",
        (message, now(), job["id"]),
    )
    if job["kind"] == "interpret":
        # A conversa não fica travada: cai para a interpretação por regras.
        payload = loads(job["payload"], {})
        g.user_id = job["user_id"]
        if payload.get("mode") == "creative":
            current_app.logger.warning("interpretação %s falhou (%s); usando regras", job["id"], message)
            creative_fallback(payload)
            return jsonify({"ok": True})
        last_user = next((m["content"] for m in reversed(payload.get("history") or []) if m["role"] == "user"), "")
        interpret_with_rules(
            payload["conversation_id"],
            last_user,
            payload.get("previous_briefing"),
            payload.get("assistant_message_id"),
            insert=False,
            note="Confira os dados do encarte no painel ao lado.",
        )
    return jsonify({"ok": True})
