"""Rotas do SiteGenius Studio (usuário em /studio, workers em /studio/worker)."""
import hashlib
import io
import hmac
import os
import re
import secrets
import time
from functools import wraps
from urllib.parse import urlsplit

import requests

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
from . import edits
from . import flyerchat
from . import layouts
from . import storage
from . import textplan
from . import typography
from .db import dumps, get_conn, loads, new_id, now
from .products import find_photo, lookup_ean, search_name, wants_internet_photos
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
    for opts in (data.get("opcoes_foto") or {}).values() if isinstance(data.get("opcoes_foto"), dict) else []:
        ids += [o.get("asset_id") for o in opts or [] if isinstance(o, dict)]
    ref = ((data.get("layout") or {}).get("referencia") or {}) if isinstance(data.get("layout"), dict) else {}
    ids += [ref.get("asset_id")] + list(ref.get("asset_ids") or [])
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
    if kind == "refedit":  # capacidade anunciada pelo agente do Acer v2.5 ao buscar tarefas
        ids = [f"{wid}:refedit" for wid, w in cfg().workers.items() if w["kind"] == "render"]
    elif kind == "tools":  # ferramentas da HostGator (referências, busca web), com a credencial do intérprete
        ids = [f"{wid}:tools" for wid, w in cfg().workers.items() if w["kind"] == "interpret"]
    else:
        ids = [wid for wid, w in cfg().workers.items() if w["kind"] == kind]
    if not ids:
        return False
    marks = ",".join("?" for _ in ids)
    row = get_conn().one(f"SELECT MAX(last_seen) AS t FROM sg_worker_status WHERE worker_id IN ({marks})", ids)
    if row and row["t"] and now() - row["t"] <= WORKER_ONLINE_S:
        return True
    if kind == "tools":  # ocupado com uma análise/encarte longos: não pede tarefa, mas está vivo
        busy = get_conn().one("SELECT id FROM sg_jobs WHERE gen_type IN ('layout_ref', 'websearch', 'flyer_ai') AND "
                              "status IN ('claimed', 'running') AND updated_at > ?", (now() - FLYER_AI_TIMEOUT_S,))
        return bool(busy)
    return False


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
        attachments.sort(key=lambda a: attachment_ids.index(a["id"]))  # "a primeira", "a segunda": ordem do envio

    ts = now()
    add_message(conv["id"], "user", texto, attachments)
    if conv["title"] in (NEW_TITLE, "Novo encarte") and texto:
        conn.run("UPDATE sg_conversations SET title = ? WHERE id = ?", (texto.splitlines()[0][:60], conv["id"]))

    options = data.get("opcoes") if isinstance(data.get("opcoes"), dict) else {}
    mode = conv.get("mode") or "ad"  # conversas antigas (antes da criação livre) eram encartes
    previous_result = last_result_asset(conv["id"])
    # Cada trabalho tem o seu estado: o encarte (briefing) e a criação livre (spec) não se misturam.
    if mode != "ad" and re.search(r"volt\w*\s+(?:ao|para o|pro|no)\s+encarte", texto, re.IGNORECASE) and             (loads(conv["briefing"]) or {}).get("produtos"):
        conn.run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))
        briefing = loads(conv["briefing"])
        add_message(conv["id"], "assistant", "De volta ao encarte! " + flyerchat.summary(briefing, []))
        return jsonify({"ok": True}), 201
    if mode == "ad" and creative.NEW_REQUEST.search(texto) and not FLYER_WORDS.search(texto) and not attachments             and not flyerchat.wants_generation(texto) and not flyerchat.NEW_VERSION.search(flyerchat.fold(texto)):
        # Pedido de outra arte no meio do encarte: muda de trabalho sem levar produtos/preços (o encarte fica guardado).
        conn.run("UPDATE sg_conversations SET mode = 'free' WHERE id = ?", (conv["id"],))
        conv = {**conv, "mode": "free", "spec": None}
        return creative_message(conv, texto, attachments, options, None)
    if mode != "ad" and creative.classify(texto, bool(previous_result)) != "ad_composite":
        return creative_message(conv, texto, attachments, options, previous_result)
    if mode != "ad":
        conn.run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))

    previous = loads(conv["briefing"]) or bf.normalize_briefing({})
    previous = apply_attachments(previous, attachments)
    assistant_id = new_id()
    images = [a["id"] for a in attachments if a["kind"] in ("reference", "product", "logo")]
    style_refs = [a["id"] for a in attachments if a["kind"] == "reference"]
    if creative.wants_commercial_redesign(texto, images) and not previous.get("produtos"):
        interpret_with_rules(conv["id"], texto, previous, assistant_id, insert=True, note=creative.REDESIGN_REPLY)
        return jsonify({"ok": True}), 201
    if style_refs and previous.get("produtos"):
        # Referência visual: análise estruturada (organização, hierarquia, cores, preços) com os produtos do cliente.
        previous = start_reference_analysis(conv, previous, style_refs[:3], flyerchat.wants_generation(texto)
                                            or bool(flyerchat.EXPLICIT_GEN.search(flyerchat.fold(texto))), texto)
    handled = flyer_chat(conv, texto, previous, assistant_id)
    if handled:
        return handled
    if wants_internet_photos(texto) and previous.get("produtos"):
        # "Pega da internet a foto": busca de verdade (bases abertas), conferindo se o resultado é o produto.
        reply = fetch_product_photos(conv, previous)
        if len(texto.split()) <= 8:  # só o pedido de fotos
            add_message(conv["id"], "assistant", reply, message_id=assistant_id)
            return jsonify({"ok": True}), 201
        add_message(conv["id"], "assistant", reply)  # e segue para registrar o resto da mensagem (loja, formato…)
        previous = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
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


CLAIM_SENTENCE = re.compile(r"[^.!?\n]*\b(gerando|vou gerar|estou gerando|criando o encarte|criando a arte|"
                            r"montando o encarte|já estou criando|vou criar o encarte agora|gerarei)\b[^.!?\n]*[.!?]?",
                            re.IGNORECASE)


def start_art_direction(conv, user_id, final, signature):
    """Encarte pelo diretor de arte (HostGator): plano do modelo, montagem no Chromium, avaliação estética e refinamento.
    A mensagem fica "pendente" até a arte chegar (o cliente vê o andamento; nada é afirmado antes)."""
    running = get_conn().one("SELECT id FROM sg_jobs WHERE conversation_id = ? AND gen_type = 'flyer_ai' AND status IN "
                             "('queued', 'claimed', 'running')", (conv["id"],))
    if running:
        return "Já estou criando o seu encarte; ele aparece aqui assim que ficar pronto."
    layout = final.get("layout") or {}
    msg_id = new_id()
    add_message(conv["id"], "assistant", "Criando o seu encarte: direção de arte, montagem e revisão visual da composição "
                                         "(leva de 1 a 3 minutos)…", status="pending", message_id=msg_id)
    comp = layout.get("composicao") or {}
    instructions = " ".join(x for x in [layout.get("instrucoes") or "",
                                         ("Pedidos de composição: " + ", ".join(f"{k}={v}" for k, v in comp.items()))
                                         if comp else ""] if x)
    payload = {"mode": "flyer_design", "conversation_id": conv["id"], "assistant_message_id": msg_id, "briefing": final,
               "asset_ids": [p["asset_id"] for p in final.get("produtos") or [] if p.get("asset_id")],
               "referencia": (layout.get("referencia") or {}).get("spec") if layout.get("modelo") == "referencia" else None,
               "instrucoes": instructions[:600], "design_anterior": layout.get("design"),
               "semente": int(layout.get("semente") or 1), "assinatura": signature}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, gen_type, created_at, updated_at) "
        "VALUES (?, ?, ?, 'interpret', 'queued', ?, 'flyer_ai', ?, ?)",
        (new_id(), user_id, conv["id"], dumps(payload), now(), now()),
    )
    return ""


MIN_FLYER_SCORE = 6.5  # abaixo disso, a arte vira rascunho (o diretor de arte reprovou a própria peça)


def handle_flyer_design(job, payload, data):
    """Arte pronta (já revisada pelo diretor de arte): vira o resultado da conversa, com os dados conferidos."""
    from . import htmlflyer  # noqa: PLC0415

    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
    g.user_id = conv["user_id"]
    import base64  # noqa: PLC0415

    try:
        image = sanitize_image(base64.b64decode(data.get("imagem") or ""), 12 * 1024 * 1024, 40_000_000, 4096)
    except (ImageRejected, ValueError):
        side_job_failed(payload)
        return
    final = payload["briefing"]
    design = htmlflyer.normalize(data.get("design") if isinstance(data.get("design"), dict) else {})
    try:
        nota = round(float(data.get("nota")), 1)
    except (TypeError, ValueError):
        nota = None
    problems = [bf._clean_text(x, 160) for x in (data.get("problemas") or [])[:4] if isinstance(x, str) and x.strip()]
    no_photo = [p["nome"] for p in final.get("produtos") or [] if not p.get("asset_id")]
    if nota is not None and nota < MIN_FLYER_SCORE:
        # Não publica uma arte que o próprio diretor de arte reprovou: mostra como rascunho, diz o porquê e o que falta.
        draft = store_asset(conv["user_id"], "preview", *image)
        missing = (f" Para ficar profissional, faltam fotos de: {', '.join(no_photo)} (envie aqui ou peça para eu buscar)."
                   if no_photo else "")
        text = (f"Fiz uma versão, mas na minha revisão ela ficou abaixo do nível para divulgar (nota {nota:g}/10), então "
                f"não a publiquei como arte final." + (f" Problemas que vi: {'; '.join(problems)}." if problems else "")
                + missing + " Veja o rascunho e me diga o que ajustar, ou peça “outra versão”.")
        get_conn().run("UPDATE sg_messages SET content = ?, status = 'ok', attachments = ? WHERE id = ? AND conversation_id = ?",
                       (text, dumps([{"id": draft, "kind": "preview"}]), payload["assistant_message_id"], conv["id"]))
        current = loads(conv["briefing"]) or final
        save_briefing(conv["id"], {**current, "layout": {**(current.get("layout") or {}), "design": design}})
        return
    asset_id = store_asset(conv["user_id"], "result", *image)
    job_id, ts = new_id(), now()
    rec = {"version": 2, "gen_type": "flyer", "briefing": final, "size": [image[2], image[3]],
           "assinatura": payload.get("assinatura"), "design": design, "nota": nota,
           "relatorio": data.get("relatorio") if isinstance(data.get("relatorio"), dict) else None}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, progress, payload, status_message, result, "
        "schema_version, gen_type, aspect_ratio, render_mode, created_at, updated_at, started_at, finished_at) "
        "VALUES (?, ?, ?, 'render', 'done', 100, ?, 'Pronto', ?, 2, 'flyer', ?, 'layout', ?, ?, ?, ?)",
        (job_id, conv["user_id"], conv["id"], dumps(rec)[:200000], dumps({"asset_id": asset_id}), final.get("formato"),
         ts, ts, ts, ts),
    )
    current = loads(conv["briefing"]) or final
    lay = {**(current.get("layout") or {}), "design": design, "gerado_assinatura": payload.get("assinatura")}
    save_briefing(conv["id"], {**current, "layout": lay, "gerado": True})
    rounds = len((rec["relatorio"] or {}).get("rodadas") or [])
    text = (f"Pronto! Criei o encarte com {len(final.get('produtos') or [])} produto(s), nomes e preços exatamente como "
            f"estão no briefing. Revisei a composição {rounds} vez(es)" + (f" (nota final {nota:g}/10)" if nota is not None else "")
            + "." + (f" Sem foto (aparece só o nome): {', '.join(no_photo)}." if no_photo else "")
            + " Pode pedir ajustes por aqui (cores, destaque, organização, outra versão).")
    set_message(payload["assistant_message_id"], conv["id"], text, job_id)


def start_reference_analysis(conv, briefing, asset_ids, generate_after, texto=""):
    """Envia a(s) referência(s) para a análise visual (ferramentas da HostGator). Devolve o briefing atualizado."""
    asset_ids = [asset_ids] if isinstance(asset_ids, str) else list(asset_ids)[:3]
    asset_id = asset_ids[0]
    lay = dict(briefing.get("layout") or {})
    ref = lay.get("referencia") or {}
    if (ref.get("asset_ids") or [ref.get("asset_id")]) == asset_ids and ref.get("estado") in ("analisando", "pronta"):
        if generate_after and ref.get("estado") == "analisando":
            lay["gerar_apos_analise"] = True
        lay["modelo"] = "referencia"
        briefing = {**briefing, "layout": lay}
        save_briefing(conv["id"], briefing)
        return briefing
    if not workers_online("tools"):
        add_message(conv["id"], "assistant", "Recebi a imagem de referência, mas a análise visual está indisponível agora. "
                                             "Vou seguir com uma composição criativa; se quiser, envie a referência de novo "
                                             "daqui a pouco.")
        return briefing
    msg_id = new_id()
    add_message(conv["id"], "assistant", "Analisando a organização da sua imagem de referência (distribuição, preços, "
                                         "cores e elementos)…", status="pending", message_id=msg_id)
    payload = {"mode": "layout_ref", "conversation_id": conv["id"], "assistant_message_id": msg_id, "asset_id": asset_id,
               "asset_ids": asset_ids, "texto": (texto or "")[:600]}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, gen_type, created_at, updated_at) "
        "VALUES (?, ?, ?, 'interpret', 'queued', ?, 'layout_ref', ?, ?)",
        (new_id(), g.user_id, conv["id"], dumps(payload), now(), now()),
    )
    lay.update({"referencia": {"asset_id": asset_id, "asset_ids": asset_ids, "estado": "analisando"}, "modelo": "referencia",
                "gerar_apos_analise": bool(generate_after)})
    briefing = {**briefing, "layout": lay, "referencias": list(dict.fromkeys((briefing.get("referencias") or []) + asset_ids))}
    save_briefing(conv["id"], bf.normalize_briefing(briefing, owned_asset_ids=owned_asset_ids(
        g.user_id, briefing_asset_ids(briefing) + asset_ids), trust_prices=True))
    return loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])


def handle_layout_ref(job, payload, data):
    """Análise da referência pronta: guarda a especificação, explica o que entendeu e gera se o cliente já pediu."""
    from . import refstyle  # noqa: PLC0415

    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
    g.user_id = conv["user_id"]
    briefing = loads(conv["briefing"]) or bf.normalize_briefing({})
    lay = dict(briefing.get("layout") or {})
    specs = [refstyle.sanitize(x) for x in (data.get("layout_refs") or [data.get("layout_ref")])][:3]
    asset_ids = (payload.get("asset_ids") or [payload["asset_id"]])[:len(specs)]
    pairs = [(a, x) for a, x in zip(asset_ids, specs) if x]
    roles = refstyle.parse_roles(payload.get("texto") or "", len(pairs))
    spec = refstyle.merge([x for _, x in pairs], roles) if pairs else None
    msg_id = payload["assistant_message_id"]
    if not spec:
        lay["referencia"] = {"asset_id": payload["asset_id"], "estado": "falhou"}
        lay["modelo"] = "criativo" if lay.get("modelo") == "referencia" else lay.get("modelo")
        lay["gerar_apos_analise"] = False
        save_briefing(conv["id"], {**briefing, "layout": lay})
        set_message(msg_id, conv["id"], "Não consegui identificar a organização dessa imagem (produtos e preços). Posso "
                                        "seguir com uma composição criativa — diga “pode gerar” — ou você pode enviar "
                                        "outra referência.")
        return
    resumo = refstyle.describe(spec)
    lay.update({"referencia": {"asset_id": pairs[0][0], "asset_ids": [a for a, _ in pairs], "estado": "pronta",
                               "spec": spec, "specs": [x for _, x in pairs], "papeis": roles, "resumo": resumo},
                "modelo": "referencia"})
    pending = lay.pop("gerar_apos_analise", False)
    briefing = {**briefing, "layout": lay}
    owned = owned_asset_ids(conv["user_id"], briefing_asset_ids(briefing) + [a for a, _ in pairs])
    briefing = bf.normalize_briefing(briefing, owned_asset_ids=owned, trust_prices=True)
    save_briefing(conv["id"], briefing)
    n = len(briefing.get("produtos") or [])
    many = len(pairs) > 1
    text = (f"Analisei {'as suas ' + str(len(pairs)) + ' referências' if many else 'a sua referência'}: {resumo}. "
            f"{refstyle.roles_text(roles, len(pairs))} Vou seguir essa organização com os seus {n} produto(s), preços, "
            f"fotos e a sua loja — nada de nomes, preços ou marcas das imagens de exemplo.").replace("  ", " ")
    job_id = None
    if pending:
        out, job_id = generate_flyer(conv, conv["user_id"], briefing)
        text += " " + out
        set_message(msg_id, conv["id"], text, job_id)
        return
    attachments = []
    if n:
        try:
            data_img = layouts.render(briefing, _flyer_assets(conv["user_id"], briefing), width=480)
            with typography.Image.open(typography.io.BytesIO(data_img)) as img:
                w, h = img.size
            attachments = [{"id": store_asset(conv["user_id"], "preview", data_img, "image/jpeg", w, h), "kind": "preview"}]
            text += " Veja a prévia. Diga “pode gerar” ou peça ajustes (cores, destaque, formato)."
        except Exception:  # noqa: BLE001
            current_app.logger.exception("prévia da referência falhou")
    get_conn().run("UPDATE sg_messages SET content = ?, status = 'ok', attachments = ? WHERE id = ? AND conversation_id = ?",
                   (text, dumps(attachments), msg_id, conv["id"]))


FLYER_WORDS = re.compile(r"encarte|panfleto|tabloide|ofertas?|promo[cç]|produto|pre[cç]o|r\$|\d,\d{2}|reais|"
                         r"layout|estilo|story|formato|fundo|foto|gerar|destaque|loja", re.IGNORECASE)


def flyer_chat(conv, texto, previous, assistant_id):
    """Encarte pela conversa (operações sobre o briefing). Devolve a resposta HTTP ou None (segue para o modelo)."""
    updated, done, actions, _ = flyerchat.apply(texto, previous)
    if (not done and not actions) or not flyerchat.all_understood(texto):
        return None  # algo que o leitor rápido não entende: o modelo interpreta a mensagem inteira
    priced = sum(2 if "(de R$" in d else 1 for d in done if "R$" in d)  # "de 14,99 por 9,99" = 1 item, 2 valores
    command = any(a.startswith(("foto:", "voltar_encarte:", "listar_encartes")) for a in actions)
    if not command and not flyerchat.numbers_accounted(texto, updated):
        return None  # sobrou número sem dono ("leve 2", "1 só é 55"): o modelo interpreta a mensagem inteira
    if not flyerchat.understood_all_prices(texto, priced + (1 if re.search(r"\d,\d{2}|r\$", texto, re.I) and
                                                               any(d.startswith("preço") for d in done) else 0)):
        return None  # há preço que o leitor rápido não entendeu: o modelo interpreta a mensagem inteira
    flyer_cmd = next((a for a in actions if a.startswith("voltar_encarte:")), None)
    if flyer_cmd is not None or "listar_encartes" in actions:
        conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (conv["id"],))
        items = archived_flyers(conv)
        if flyer_cmd is not None:
            target = find_flyer(items, flyer_cmd.split(":", 1)[1])
            if not target:
                names = ", ".join(e["nome"] for e in items) or "nenhum outro"
                add_message(conv["id"], "assistant", f"Não encontrei esse encarte. Encartes guardados nesta conversa: {names}.",
                            message_id=assistant_id)
                return jsonify({"ok": True}), 201
            switch_flyer(conv, previous, target["id"])
            briefing = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
            add_message(conv["id"], "assistant", f"De volta ao encarte “{target['nome']}”. " + flyerchat.summary(briefing, []),
                        message_id=assistant_id)
            return jsonify({"ok": True}), 201
        names = [e["nome"] for e in items]
        text = (f"Encarte aberto agora: {flyer_label(previous)}." + (f" Guardados: {', '.join(names)}. Para trocar, diga "
                f"“volta pro encarte {names[-1].split()[-1].lower()}”." if names else " Não há outros encartes nesta conversa."))
        add_message(conv["id"], "assistant", text, message_id=assistant_id)
        return jsonify({"ok": True}), 201
    if "novo_encarte" in actions and previous.get("produtos"):
        conv_row = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (conv["id"],))
        archive_current(conv_row, previous)
        fresh = bf.normalize_briefing({})
        updated, done2, actions2, _ = flyerchat.apply(texto, {**fresh, "encarte_id": new_id()})
        done = ["novo encarte (o anterior ficou guardado: “" + flyer_label(previous) + "”)"] + done2
        actions = {a for a in actions2 if a != "novo_encarte"}
        previous = fresh
    choice = next((a for a in actions if a.startswith("foto:")), None)
    if choice:
        if choice == "foto:?":
            names = ", ".join((previous.get("opcoes_foto") or {}).keys())
            add_message(conv["id"], "assistant", f"De qual produto? Tenho opções para: {names}. Ex.: “usa a 2 do "
                                                 f"{names.split(',')[0].split()[0].lower()}”.", message_id=assistant_id)
            return jsonify({"ok": True}), 201
        _, idx, name = choice.split(":", 2)
        text = choose_photo(conv, updated, int(idx), name)
        briefing = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
        add_message(conv["id"], "assistant", text + " " + flyerchat.summary(briefing, []), message_id=assistant_id)
        return jsonify({"ok": True}), 201
    texts = user_texts(conv["id"])
    owned = owned_asset_ids(g.user_id, briefing_asset_ids(updated))
    normalized = bf.normalize_briefing(updated, texts + confirmed_prices(previous) + [texto], owned)
    save_briefing(conv["id"], normalized)
    replies, attachments, job_id = [flyerchat.summary(normalized, done)] if done else [], [], None
    ref_now = (normalized.get("layout") or {}).get("referencia") or {}
    if "referencia" in actions and len(ref_now.get("specs") or []) > 1:
        from . import refstyle  # noqa: PLC0415

        roles = refstyle.parse_roles(texto, len(ref_now["specs"]))
        if roles != ref_now.get("papeis"):
            ref_now = {**ref_now, "papeis": roles, "spec": refstyle.merge(ref_now["specs"], roles)}
            normalized = {**normalized, "layout": {**normalized["layout"], "referencia": ref_now, "modelo": "referencia"}}
            save_briefing(conv["id"], bf.normalize_briefing(normalized, owned_asset_ids=owned_asset_ids(
                g.user_id, briefing_asset_ids(normalized)), trust_prices=True))
            normalized = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
            replies.append(refstyle.roles_text(roles, len(ref_now["specs"])))
            actions.add("gerar" if normalized.get("gerado") else "previas")
    if "referencia" in actions and not (normalized.get("layout") or {}).get("referencia") and normalized.get("referencias"):
        normalized = start_reference_analysis(conv, normalized, normalized["referencias"][-1], "gerar" in actions)
    if "buscar_fotos" in actions:
        # a busca já está acontecendo: tira o convite "Sem foto… posso buscar" do resumo (não se contradiz)
        replies = [re.sub(r"\s*Sem foto: .*?envia as fotos aqui\.", "", r) for r in replies]
        replies.append(fetch_product_photos(conv, normalized))
        attachments += getattr(g, "photo_sheets", [])
        normalized = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
    if "trocar_foto" in actions:
        replies.append(swap_product_photo(conv, normalized, texto))
        normalized = loads(get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (conv["id"],))["briefing"])
    if "previas" in actions:
        text, attachments, normalized = flyer_previews(conv, normalized)
        replies.append(text)
    if "gerar" in actions:
        text, job_id = generate_flyer(conv, g.user_id, normalized)
        replies.append(text)
    text = " ".join(r for r in replies if r)
    if text or attachments or job_id:
        add_message(conv["id"], "assistant", text, attachments or None, message_id=assistant_id, job_id=job_id)
    return jsonify({"ok": True}), 201


def _flyer_assets(user_id, briefing):
    ids = [p.get("asset_id") for p in briefing.get("produtos") or [] if p.get("asset_id")]
    out = {}
    for aid in ids:
        row = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (aid, user_id))
        if row:
            out[aid] = asset_bytes(row)
    return out


def flyer_previews(conv, briefing):
    """Prévias de 3 estilos com os produtos/preços/fotos reais; o cliente escolhe pela conversa."""
    if not briefing.get("produtos"):
        return "Primeiro me diga os produtos e preços; aí te mostro os estilos com eles.", [], briefing
    lay = briefing.get("layout") or {}
    shown = lay.get("previas") or []
    seed = int(lay.get("semente") or 1)
    pool = (["referencia"] if (lay.get("referencia") or {}).get("spec") else []) + \
        [f"criativo:{seed + i}" for i in range(4)] + layouts.ORDER
    pool = [m for m in pool if m not in shown] or pool
    chosen = pool[:3]
    assets = _flyer_assets(g.user_id, briefing)
    attachments = []
    for modelo, data in layouts.previews(briefing, assets, chosen, width=420):
        with typography.Image.open(typography.io.BytesIO(data)) as img:
            w, h = img.size
        attachments.append({"id": store_asset(g.user_id, "preview", data, "image/jpeg", w, h), "kind": "preview"})
    briefing = {**briefing, "layout": {**(briefing.get("layout") or {}), "previas": chosen}}
    save_briefing(conv["id"], bf.normalize_briefing(briefing, owned_asset_ids=owned_asset_ids(
        g.user_id, briefing_asset_ids(briefing)), trust_prices=True))
    names = ", ".join(f"{i + 1}) {layouts.preview_name(m)}" for i, m in enumerate(chosen))
    return (f"Fiz 3 opções com os seus produtos: {names}. Diga qual prefere (ex.: “gostei do segundo”) e o que mudar "
            "— cores, produto em destaque, formato. Se quiser outras, peça “outros estilos”."), attachments, briefing


def generate_flyer(conv, user_id, briefing, local=False):
    """Gera o encarte com o layout escolhido (composição no servidor: fotos, nomes e preços exatos do briefing)."""
    ok, motivo = bf.ready_for_render(briefing)
    if not ok:
        return f"Ainda não dá para gerar: {motivo}. Me diga e eu atualizo.", None
    layout = {**(briefing.get("layout") or {})}
    ref = layout.get("referencia") or {}
    if ref.get("estado") == "analisando":
        save_briefing(conv["id"], {**briefing, "layout": {**layout, "gerar_apos_analise": True}})
        return ("Estou terminando a análise da sua imagem de referência. Assim que ela ficar pronta eu gero o encarte "
                "automaticamente, sem você precisar pedir de novo."), None
    problem = render_quota_problem(user_id)
    if problem:
        return problem, None
    layout["modelo"] = layout.get("modelo") or ("referencia" if ref.get("spec") else "criativo")
    layout["gerar_apos_analise"] = False
    final = {**briefing, "layout": layout}
    visible_layout = {k: v for k, v in layout.items() if k not in ("design", "gerado_assinatura", "previas",
                                                                    "gerar_apos_analise", "instrucoes")}
    signature = hashlib.sha256(dumps({**{k: final.get(k) for k in ("produtos", "loja", "titulo", "formato", "validade",
                                                                   "logo_asset_id")}, "layout": visible_layout}).encode()
                               ).hexdigest()[:24]
    last = get_conn().one("SELECT id, payload, created_at FROM sg_jobs WHERE conversation_id = ? AND gen_type = 'flyer' "
                          "AND status = 'done' ORDER BY created_at DESC LIMIT 1", (conv["id"],))
    if last and (loads(last["payload"], {}) or {}).get("assinatura") == signature and now() - last["created_at"] < 900:
        return ("Esse encarte já está pronto logo acima — nada mudou desde a última geração. Quer outra versão? "
                "É só pedir “outra versão” ou dizer o que mudar."), None
    if not local and not layout.get("fundo_ia") and workers_online("tools"):
        return start_art_direction(conv, user_id, final, signature), None
    if layout.get("fundo_ia"):
        # Fundo gerado pelo Acer (Q3, sem textos nem produtos); o encarte é montado aqui quando o fundo chegar.
        fmt = bf.FORMATS.get(final.get("formato"), bf.FORMATS["feed_4x5"])
        aspect = {"story": "9:16", "quadrado": "1:1", "feed_4x5": "4:5", "a4": "3:4"}.get(final.get("formato"), "4:5")
        spec = creative.build_spec(prompt_original="fundo do encarte", prompt_visual=bf.scene_prompt(final), style="auto",
                                   aspect=aspect, quality="qualidade", user_texts=[])
        spec["encarte"] = final
        job_id, problem = enqueue_free_image(conv, user_id, spec)
        if problem:
            return problem, None
        save_briefing(conv["id"], {**briefing, "layout": layout, "gerado": True})
        return ("Certo! Vou criar um fundo com imagem e montar o encarte em cima, com as fotos, nomes e preços "
                "exatamente como estão no briefing."), job_id
    try:
        data = layouts.render(final, _flyer_assets(user_id, final))
        with typography.Image.open(typography.io.BytesIO(data)) as img:
            w, h = img.size
        asset_id = store_asset(user_id, "result", data, "image/jpeg", w, h)
    except Exception:  # noqa: BLE001 — o cliente sabe que não saiu e pode pedir de novo
        current_app.logger.exception("composição do encarte falhou na conversa %s", conv["id"])
        return ("Não consegui montar o encarte desta vez (falha ao compor a arte). Seus produtos e preços continuam "
                "salvos; diga “gerar” para eu tentar de novo."), None
    job_id, ts = new_id(), now()
    payload = {"version": 2, "gen_type": "flyer", "briefing": final, "size": [w, h], "assinatura": signature}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, progress, payload, status_message, result, "
        "schema_version, gen_type, aspect_ratio, render_mode, created_at, updated_at, started_at, finished_at) "
        "VALUES (?, ?, ?, 'render', 'done', 100, ?, 'Pronto', ?, 2, 'flyer', ?, 'layout', ?, ?, ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload), dumps({"asset_id": asset_id}), briefing.get("formato"), ts, ts, ts, ts),
    )
    save_briefing(conv["id"], {**briefing, "layout": layout, "gerado": True})
    no_photo = [p["nome"] for p in briefing["produtos"] if not p.get("asset_id")]
    note = (f" Sem foto (aparece só o nome): {', '.join(no_photo)} — envie a foto ou peça para eu buscar." if no_photo else "")
    return (f"Pronto! Gerei o encarte ({layouts.preview_name(layout['modelo'])}) com "
            f"{len(briefing['produtos'])} produto(s), com os nomes e preços exatamente como estão no briefing.{note} "
            "Pode pedir ajustes por aqui (preço, ordem, destaque, cores, estilo)."), job_id


def swap_product_photo(conv, briefing, texto):
    """"Troca a foto do segundo produto": próxima opção confiável das bases abertas para aquele produto."""
    products = briefing.get("produtos") or []
    target = flyerchat._find(products, texto)
    if not target:
        return "De qual produto você quer trocar a foto?"
    current = get_conn().one("SELECT source_url FROM sg_assets WHERE id = ?", (target.get("asset_id"),)) \
        if target.get("asset_id") else None
    try:
        options = [c for c in search_name(target["nome"], limit=12) if match_score_ok(target["nome"], c)
                   and c["image_url"] != (current or {}).get("source_url")]
    except Exception:  # noqa: BLE001
        options = []
    if not options:
        return f"Não encontrei outra foto confiável de {target['nome']}. Se quiser, envie a foto aqui."
    cand = options[0]
    try:
        raw = safe_download(cand["image_url"], cfg().max_upload_bytes * 2)
        image, mime, width, height = sanitize_image(raw, cfg().max_upload_bytes * 2, cfg().max_image_pixels,
                                                    cfg().max_image_side)
    except (UnsafeUrl, ImageRejected):
        return f"A outra foto de {target['nome']} não pôde ser usada. Se quiser, envie a foto aqui."
    target["asset_id"] = store_asset(g.user_id, "product", image, mime, width, height, cand["image_url"],
                                     cand["license"], cand["attribution"])
    save_briefing(conv["id"], bf.normalize_briefing(briefing, owned_asset_ids=owned_asset_ids(
        g.user_id, briefing_asset_ids(briefing)), trust_prices=True))
    return f"Troquei a foto de {target['nome']} por: {cand['nome']} ({cand.get('marca') or 'base aberta'})."


def match_score_ok(name, cand):
    from .products import match_score  # noqa: PLC0415

    return match_score(name, cand) >= 1.0


def _own_copy(user_id, asset_id):
    """Foto do catálogo compartilhado (licença aberta) vira um asset do próprio cliente."""
    row = get_conn().one("SELECT * FROM sg_assets WHERE id = ?", (asset_id,))
    if not row:
        return None
    if row["user_id"] == user_id:
        return asset_id
    try:
        data = asset_bytes(row)
    except Exception:  # noqa: BLE001 — armazenamento lento/indisponível: segue sem a foto (busca continua), sem erro 500
        current_app.logger.exception("não foi possível copiar a foto do catálogo %s", asset_id)
        return None
    return store_asset(user_id, row["kind"] if row["kind"] in ("product", "candidate") else "product", data,
                       row["mime"], row["width"], row["height"], row.get("source_url"), row.get("license"), row.get("attribution"))


def _download_candidate(user_id, cand, kind="candidate"):
    """Baixa e confere a foto (formato, tamanho, não é só o código de barras). Devolve asset_id ou None."""
    from .products import looks_like_barcode  # noqa: PLC0415

    try:
        raw = safe_download(cand["image_url"], cfg().max_upload_bytes * 2)
        image, mime, width, height = sanitize_image(raw, cfg().max_upload_bytes * 2, cfg().max_image_pixels,
                                                    cfg().max_image_side)
    except (UnsafeUrl, ImageRejected, requests.RequestException):  # rede lenta/fora: sem foto, nunca erro 500
        return None
    if looks_like_barcode(image) or min(width, height) < 120:
        return None
    return store_asset(user_id, kind, image, mime, width, height, cand["image_url"], cand.get("license"),
                       cand.get("attribution"))


OPEN_LICENSE = "licença aberta (CC BY-SA 3.0, {fonte})"


def options_sheet(user_id, product_name, options):
    """Folha com as opções numeradas (1, 2, 3) para o cliente escolher pela conversa."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from . import composer  # noqa: PLC0415

    tiles = []
    for opt in options:
        row = get_conn().one("SELECT * FROM sg_assets WHERE id = ?", (opt["asset_id"],))
        if row:
            img = Image.open(io.BytesIO(asset_bytes(row))).convert("RGB")
            img.thumbnail((300, 300))
            tiles.append((img, opt))
    if not tiles:
        return None
    W = 330 * len(tiles) + 20
    sheet = Image.new("RGB", (W, 420), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for k, (img, opt) in enumerate(tiles):
        x = 20 + k * 330
        sheet.paste(img, (x + (300 - img.width) // 2, 20 + (300 - img.height) // 2))
        d.ellipse((x, 10, x + 44, 54), fill=(215, 25, 32))
        d.text((x + 22, 32), str(k + 1), font=composer.font("poppins", 800, 26), fill=(255, 255, 255), anchor="mm")
        f, lines = composer.wrap_fit(opt["nome"], "poppins", 600, 300, 52, 2, 11)
        composer.draw_lines(d, lines, f, (x, 326, x + 300, 380), (30, 30, 34))
        d.text((x + 150, 396), opt.get("fonte", "")[:40], font=composer.font("poppins", 500, 13), fill=(110, 110, 116), anchor="mm")
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=88)
    return store_asset(user_id, "preview", out.getvalue(), "image/jpeg", sheet.width, sheet.height)


def fetch_product_photos(conv, briefing):
    """Fotos para os produtos sem foto: 1) catálogo do cliente/compartilhado, 2) bases abertas, 3) internet (navegador
    na HostGator, assíncrono). Só escolhe sozinho quando marca, variante e apresentação conferem; senão, opções."""
    from . import catalog  # noqa: PLC0415

    found, missing, choice_msgs, sheets = [], [], [], []
    opcoes = dict(briefing.get("opcoes_foto") or {})
    to_web = []
    t0 = time.monotonic()
    for product in briefing.get("produtos") or []:
        if product.get("asset_id"):
            continue
        if time.monotonic() - t0 > 7:  # orçamento da requisição (o Vercel corta em 15 s): o resto vai para a busca em 2º plano
            to_web.append(product["nome"])
            continue
        exact, cat_opts = catalog.lookup(g.user_id, product)
        if exact:
            product["asset_id"] = _own_copy(g.user_id, exact["asset_id"])
            if product["asset_id"]:
                found.append(f"{product['nome']} → {exact['nome']} (do seu catálogo)")
                continue
        cand, off_opts = None, []
        try:
            cand, off_opts = find_photo(product)
        except Exception:  # noqa: BLE001 — falha da base externa não trava a conversa
            current_app.logger.exception("busca de foto falhou")
        if cand:
            asset = _download_candidate(g.user_id, cand, "product")
            if asset:
                product["asset_id"] = asset
                catalog.add(g.user_id, nome=cand["nome"] or product["nome"], asset_id=asset, marca=cand.get("marca", ""),
                            ean=cand.get("ean", ""), apresentacao=cand.get("quantidade", ""), fonte=cand["fonte"],
                            pagina=cand.get("pagina", ""), image_url=cand["image_url"], licenca=cand.get("license", ""),
                            autorizacao=OPEN_LICENSE.format(fonte=cand["fonte"]), compartilhar=True)
                found.append(f"{product['nome']} → {cand['nome'] or 'produto'}"
                             + (f" ({cand['marca']})" if cand.get("marca") else ""))
                continue
        options = [{"asset_id": r["asset_id"], "nome": r["nome"], "fonte": "seu catálogo" if r["user_id"] == g.user_id
                    else r["fonte"], "licenca": r["licenca"], "autorizacao": r["autorizacao"], "catalogo": True,
                    "pagina": r["pagina"] or ""} for r in cat_opts]
        for o in off_opts:
            if len(options) >= 3:
                break
            asset = _download_candidate(g.user_id, o)
            if asset:
                from .productmatch import label  # noqa: PLC0415

                options.append({"asset_id": asset, "nome": label(o["nome"], o.get("marca", ""), o.get("quantidade", "")),
                                "fonte": o["fonte"], "licenca": o.get("license", ""),
                                "autorizacao": OPEN_LICENSE.format(fonte=o["fonte"]), "pagina": o.get("pagina", ""),
                                "marca": o.get("marca", ""), "ean": o.get("ean", ""), "apresentacao": o.get("quantidade", "")})
        if options:
            opcoes[product["nome"]] = options[:3]
            sheet = options_sheet(g.user_id, product["nome"], options[:3])
            if sheet:
                sheets.append({"id": sheet, "kind": "preview"})
            choice_msgs.append(f"{product['nome']}: " + " / ".join(f"{k + 1}) {o['nome']}" for k, o in enumerate(options[:3])))
        if len(options) < 3:
            to_web.append(product["nome"])
        if not options:
            missing.append(product["nome"])
    texts = user_texts(conv["id"])
    owned = owned_asset_ids(g.user_id, briefing_asset_ids(briefing))
    normalized = bf.normalize_briefing({**briefing, "opcoes_foto": opcoes}, texts, owned)
    save_briefing(conv["id"], normalized)
    parts = []
    if found:
        parts.append("Coloquei as fotos (produto conferido: marca, variante e apresentação): " + "; ".join(found) + ".")
    if choice_msgs:
        parts.append("Para não errar o produto, escolha a foto certa — " + "; ".join(choice_msgs)
                     + ". Responda, por exemplo, “usa a 2 do " + choice_msgs[0].split(":")[0].split()[0].lower() + "”.")
    web_started = bool(to_web) and start_websearch(conv, [p for p in to_web])
    if web_started:
        parts.append("Também estou procurando na internet (" + ", ".join(to_web) + "); as opções chegam aqui em instantes.")
    elif missing:
        parts.append("Não encontrei foto confiável para: " + ", ".join(missing)
                     + ". Envie a foto (ou o código de barras) desses itens e eu coloco no encarte.")
    if not parts:
        parts.append("Todos os produtos já têm foto.")
    g.photo_sheets = sheets
    return " ".join(parts)


def start_websearch(conv, names):
    """Busca na internet (ferramentas da HostGator). False se indisponível."""
    if not names or not workers_online("tools"):
        return False
    msg_id = new_id()
    add_message(conv["id"], "assistant", "Procurando fotos na internet…", status="pending", message_id=msg_id)
    payload = {"mode": "websearch", "conversation_id": conv["id"], "assistant_message_id": msg_id,
               "produtos": [{"nome": n} for n in names[:8]]}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, gen_type, created_at, updated_at) "
        "VALUES (?, ?, ?, 'interpret', 'queued', ?, 'websearch', ?, ?)",
        (new_id(), g.user_id, conv["id"], dumps(payload), now(), now()),
    )
    return True


def handle_websearch(job, payload, data):
    """Opções vindas da internet: o cliente escolhe (a escolha registra a autorização de uso)."""
    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
    g.user_id = conv["user_id"]
    briefing = loads(conv["briefing"]) or bf.normalize_briefing({})
    opcoes = dict(briefing.get("opcoes_foto") or {})
    names = {p["nome"] for p in briefing.get("produtos") or [] if not p.get("asset_id")}
    lines, sheets = [], []
    for r in data.get("websearch") or []:
        name = r.get("produto")
        if name not in names:
            continue
        opts = [{"asset_id": o["asset_id"], "nome": bf._clean_text(o.get("titulo"), 90), "fonte": bf._clean_text(o.get("fonte"), 60),
                 "pagina": bf._clean_text(o.get("pagina"), 400), "licenca": "não informada (imagem de site)",
                 "autorizacao": "", "web": True}
                for o in (r.get("opcoes") or []) if o.get("asset_id") and
                owned_asset_ids(conv["user_id"], [o["asset_id"]])]
        if not opts:
            continue
        merged = (opcoes.get(name) or []) + opts
        opcoes[name] = merged[:3] if len(merged) <= 3 else (opcoes.get(name) or [])[:1] + opts[:2]
        sheet = options_sheet(conv["user_id"], name, opcoes[name])
        if sheet:
            sheets.append({"id": sheet, "kind": "preview"})
        lines.append(f"{name}: " + " / ".join(f"{k + 1}) {o['nome']} ({o['fonte']})" for k, o in enumerate(opcoes[name])))
    briefing = bf.normalize_briefing({**briefing, "opcoes_foto": opcoes},
                                     owned_asset_ids=owned_asset_ids(conv["user_id"], briefing_asset_ids(briefing)),
                                     trust_prices=True)
    save_briefing(conv["id"], briefing)
    if lines:
        text = ("Encontrei estas opções na internet — " + "; ".join(lines) + ". As imagens de sites não têm licença "
                "informada: ao escolher, você confirma que pode usá-las no seu encarte. Responda, por exemplo, “usa a 1 do "
                + lines[0].split(":")[0].split()[0].lower() + "”.")
    else:
        text = "Não encontrei na internet fotos que correspondam exatamente aos produtos (marca, variante e apresentação)."
        notes = [bf._clean_text(n, 120) for n in (data.get("notas") or [])[:3]]
        if notes:
            text += " Detalhes: " + "; ".join(notes) + "."
        text += " Você pode enviar as fotos aqui."
    get_conn().run("UPDATE sg_messages SET content = ?, status = 'ok', attachments = ? WHERE id = ? AND conversation_id = ?",
                   (text, dumps(sheets), payload["assistant_message_id"], conv["id"]))


def choose_photo(conv, briefing, idx, name):
    """“Usa a 2 do leite”: aplica a foto escolhida e registra no catálogo com a autorização do cliente."""
    from . import catalog  # noqa: PLC0415

    opcoes = dict(briefing.get("opcoes_foto") or {})
    opts = opcoes.get(name) or []
    if idx >= len(opts):
        return f"Não encontrei essa opção para {name}."
    opt = opts[idx]
    product = next((p for p in briefing["produtos"] if p["nome"] == name), None)
    if not product:
        return f"{name} não está mais no encarte."
    product["asset_id"] = opt["asset_id"]
    autorizacao = opt.get("autorizacao") or catalog.authorization_today(opt.get("fonte"))
    if not opt.get("catalogo"):
        # Guarda o que a foto É (título do produto encontrado), não a palavra do pedido: "Leite" escolhendo
        # "Leite moça" não faz toda foto de "Leite" virar leite condensado nos próximos encartes.
        catalog.add(g.user_id, nome=opt.get("nome") or product["nome"], asset_id=opt["asset_id"], marca=opt.get("marca", ""),
                    ean=opt.get("ean", ""), apresentacao=opt.get("apresentacao", ""), fonte=opt.get("fonte", ""),
                    pagina=opt.get("pagina", ""), licenca=opt.get("licenca", ""), autorizacao=autorizacao,
                    compartilhar=False, origem="escolha")
    opcoes.pop(name, None)
    texts = user_texts(conv["id"])
    normalized = bf.normalize_briefing({**briefing, "opcoes_foto": opcoes}, texts,
                                       owned_asset_ids(g.user_id, briefing_asset_ids(briefing)))
    save_briefing(conv["id"], normalized)
    note = " Registrei sua autorização de uso dessa imagem." if opt.get("web") else ""
    return f"Coloquei a foto {idx + 1} em {name}: {opt['nome']}.{note} Ela fica no seu catálogo para os próximos encartes."


# ---------------------------------------------------------------- vários encartes na mesma conversa
def flyer_label(briefing):
    if briefing.get("loja"):
        return briefing["loja"]
    names = [p["nome"] for p in briefing.get("produtos") or []][:2]
    return "encarte de " + " e ".join(names) if names else "encarte sem produtos"


def archived_flyers(conv):
    return loads(conv.get("encartes"), []) or []


def archive_current(conv, briefing):
    """Guarda o encarte atual (se tem produtos) na lista da conversa. Devolve a lista atualizada."""
    items = [e for e in archived_flyers(conv) if isinstance(e, dict)]
    if (briefing or {}).get("produtos"):
        current_id = briefing.get("encarte_id") or new_id()
        items = [e for e in items if e.get("id") != current_id]
        items.append({"id": current_id, "nome": flyer_label(briefing), "briefing": {**briefing, "encarte_id": current_id},
                      "atualizado": now()})
    items = items[-12:]
    get_conn().run("UPDATE sg_conversations SET encartes = ? WHERE id = ?", (dumps(items), conv["id"]))
    return items


def find_flyer(items, query):
    """“da farmácia”, “do mercado”, “anterior”, “primeiro”: o encarte arquivado mais parecido."""
    from .productmatch import fold  # noqa: PLC0415

    q = fold(query)
    if not items:
        return None
    if not q or re.search(r"\b(anterior|ultimo|outro)\b", q):
        return items[-1]
    if re.search(r"\bprimeir", q):
        return items[0]
    words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) >= 3 and w not in ("encarte", "panfleto", "das", "dos")]
    synonyms = {"farmacia": ("drogaria", "farma", "drog"), "drogaria": ("farmacia", "farma"), "mercado": ("supermercado",
                "mercad", "atacad"), "supermercado": ("mercado",)}

    def score(e):
        b = e.get("briefing") or {}
        hay = fold(" ".join([e.get("nome", ""), b.get("loja") or "", " ".join(p["nome"] for p in b.get("produtos") or [])]))
        return sum(1 for w in words if w in hay or any(sy in hay for sy in synonyms.get(w, ())))

    best = max(items, key=score)
    return best if score(best) > 0 else None


def switch_flyer(conv, current, target_id):
    items = archive_current(conv, current)
    target = next((e for e in items if e["id"] == target_id), None)
    if not target:
        return None
    items = [e for e in items if e["id"] != target_id]
    get_conn().run("UPDATE sg_conversations SET encartes = ?, mode = 'ad' WHERE id = ?", (dumps(items), conv["id"]))
    save_briefing(conv["id"], target["briefing"])
    return target


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


SIDE_FAILURE = {
    "evaluate": "Pronto! Desta vez não consegui conferir a imagem automaticamente — me diga se ficou como você queria.",
    "layout_ref": "Não consegui analisar a imagem de referência agora. Posso seguir com um layout criativo (diga "
                  "“pode gerar”) ou você pode tentar enviar a referência de novo.",
    "websearch": "A busca na internet não respondeu agora. Você pode enviar as fotos aqui ou pedir para eu tentar de novo.",
}


def side_job_failed(payload):
    """Tarefas auxiliares (conferência, análise de referência, busca web) que falham: a conversa segue com honestidade."""
    mode = payload.get("mode")
    if mode == "flyer_design":
        conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
        text, job_id = generate_flyer(conv, conv["user_id"], payload["briefing"], local=True)
        set_message(payload["assistant_message_id"], conv["id"], text, job_id)
        return True
    if mode not in SIDE_FAILURE:
        return False
    if payload.get("assistant_message_id") and payload.get("conversation_id"):
        set_message(payload["assistant_message_id"], payload["conversation_id"], SIDE_FAILURE[mode])
    if mode == "layout_ref":
        conv = get_conn().one("SELECT briefing FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
        briefing = loads(conv["briefing"]) if conv else None
        ref = ((briefing or {}).get("layout") or {}).get("referencia")
        if ref and ref.get("estado") == "analisando":
            ref["estado"] = "falhou"
            briefing["layout"]["gerar_apos_analise"] = False
            save_briefing(payload["conversation_id"], briefing)
    return True


FLYER_AI_TIMEOUT_S = 900  # direção de arte: plano + montagem + até 2 avaliações, com o modelo compartilhado


def resolve_stale_interpretation(conv):
    """Se o worker HostGator não respondeu a tempo, interpreta por regras e avisa."""
    conn = get_conn()
    timeout = cfg().interpret_timeout_s
    stale = conn.all(
        "SELECT * FROM sg_jobs WHERE conversation_id = ? AND kind = 'interpret' AND "
        "((COALESCE(gen_type, '') <> 'flyer_ai' AND ((status = 'queued' AND created_at < ?) OR "
        "  (status IN ('claimed', 'running') AND created_at < ?))) OR "
        " (gen_type = 'flyer_ai' AND created_at < ?))",
        (conv["id"], now() - timeout, now() - 3 * timeout, now() - FLYER_AI_TIMEOUT_S),
    )
    for job in stale:
        changed = conn.run(
            "UPDATE sg_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ? AND status = ?",
            ("tempo esgotado; usado modo regras", now(), job["id"], job["status"]),
        )
        if not changed:
            continue
        payload = loads(job["payload"], {})
        g.user_id = job["user_id"]
        if side_job_failed(payload):
            continue
        if payload.get("mode") == "creative":
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


def last_result_row(conversation_id):
    return get_conn().one(
        "SELECT id, result, payload FROM sg_jobs WHERE conversation_id = ? AND kind = 'render' AND status = 'done' "
        "AND gen_type IN ('free_image', 'text_edit') ORDER BY finished_at DESC LIMIT 1",
        (conversation_id,),
    )


def last_final_asset(conversation_id):
    """A peça final que o cliente está vendo (com os textos), para o modelo analisar visualmente."""
    row = last_result_row(conversation_id)
    return (loads(row["result"], {}) or {}).get("asset_id") if row else None


def last_result_asset(conversation_id):
    """Imagem base para ajustes: a arte SEM os textos aplicados (os textos são reaplicados depois)."""
    row = last_result_row(conversation_id)
    if not row:
        return None
    result = loads(row["result"], {}) or {}
    return result.get("art_asset_id") or result.get("asset_id")


def apply_texts(user_id, art_bytes, spec):
    """Etapas C e D: aplica o plano de textos sobre a arte. Devolve (asset final, relatório)."""
    plan = spec.get("text_plan")
    if not plan or not plan.get("blocos"):
        return None, None
    data, report = typography.compose(art_bytes, plan)
    with typography.Image.open(typography.io.BytesIO(data)) as img:
        width, height = img.size
    return store_asset(user_id, "result", data, "image/jpeg", width, height), report


def is_text_only_edit(texto, previous_spec):
    """Ajuste só de texto sobre a criação anterior (sem gerar de novo). Pedido novo ("crie…") nunca entra aqui."""
    if not previous_spec or not (previous_spec.get("text_plan") or {}).get("blocos") or previous_spec.get("pending"):
        return False
    if creative.NEW_REQUEST.search(texto):
        return False
    kind = edits.classify(texto, previous_spec["text_plan"])
    return kind["texto"] and not kind["cena"]


def enqueue_reference_read(conv, user_id, texto, reference_id):
    """Redesign: o Acer lê a arte (OCR + recorte das fotos originais) e devolve os dados para o painel."""
    if not workers_online("refedit"):
        return None, None
    asset = get_conn().one("SELECT id FROM sg_assets WHERE id = ? AND user_id = ?", (reference_id, user_id))
    if not asset:
        return None, None
    payload = {"version": 2, "gen_type": "reference_read", "asset_ids": [reference_id],
               "spec": {"gen_type": "reference_read", "prompt_original": creative.clean(texto, 2000)}}
    job_id, ts = new_id(), now()
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, gen_type, "
        "prompt_original, reference_ids, render_mode, created_at, updated_at) "
        "VALUES (?, ?, ?, 'render', 'queued', ?, 'Na fila do gerador', 2, 'reference_read', ?, ?, 'leitura', ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload), creative.clean(texto, 2000), dumps([reference_id]), ts, ts),
    )
    get_conn().run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))
    return ("Para modernizar sem trocar nenhum produto, vou ler a sua arte (produtos, preços e as fotos originais) e "
            "montar uma versão com layout atual usando exatamente essas informações. Em instantes os dados aparecem "
            "no painel para você conferir."), job_id


def enqueue_reference_edit(conv, user_id, texto, reference_id):
    """Edição direta da imagem enviada: só o que foi pedido muda; fotos, logotipo e contatos ficam como estão."""
    changes = creative.reference_changes(texto)
    problem = render_quota_problem(user_id)
    if problem:
        return problem, None
    asset = get_conn().one("SELECT id FROM sg_assets WHERE id = ? AND user_id = ?", (reference_id, user_id))
    if not asset:
        return "Não consegui abrir a imagem que você enviou. Pode anexá-la de novo?", None
    if not workers_online("refedit"):
        return ("Recebi sua imagem, mas agora não consigo alterar textos diretamente dentro dela. Se quiser, "
                "crio uma arte nova inspirada nela — é só me dizer o que manter."), None
    spec = {"version": 2, "gen_type": "reference_edit", "prompt_original": creative.clean(texto, 2000),
            "trocas": changes, "reference_ids": [reference_id], "aspect_ratio": "4:5", "style": "auto"}
    payload = {"version": 2, "gen_type": "reference_edit", "asset_ids": [reference_id], "trocas": changes,
               "instagram": True, "spec": spec}
    job_id, ts = new_id(), now()
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, gen_type, "
        "prompt_original, prompt_visual, style, aspect_ratio, reference_ids, render_mode, created_at, updated_at) "
        "VALUES (?, ?, ?, 'render', 'queued', ?, 'Na fila do gerador', 2, 'reference_edit', ?, '', 'auto', '4:5', ?, "
        "'edicao', ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload), spec["prompt_original"], dumps([reference_id]), ts, ts),
    )
    save_spec(conv["id"], {**spec, "job_id": job_id})
    what = "; ".join(f"{'aluguel/preço' if c['tipo'] == 'preco' else 'telefone'} para {c['novo']}" for c in changes)
    return (f"Certo! Vou trabalhar na imagem que você enviou: troco {what} e deixo no formato do Instagram, "
            "mantendo as fotos e os demais dados como estão."), job_id


def text_edit_job(conv, user_id, texto, previous_spec, plan=None, record=None):
    """Reaplica os textos sobre a arte anterior, sem passar pelo gerador. Devolve job_id ou None."""
    row = last_result_row(conv["id"])
    result = loads(row["result"], {}) or {} if row else {}
    art_id = result.get("art_asset_id")
    if not art_id:
        return None
    art = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (art_id, user_id))
    if not art:
        return None
    if plan is None:  # sem planejador (reserva): revisa e valida aqui
        plan = edits.revise_plan(previous_spec["text_plan"], texto)
        sources = conversation_texts(conv["id"]) + [texto] + textplan.plan_texts(previous_spec.get("text_plan"))
        plan = textplan.validate_plan(plan, sources)
    # Com o planejador, o plano já foi validado (inclui mudanças só de forma, como maiúsculas, do texto do cliente).
    if not plan:
        return None
    spec = {**previous_spec, "text_plan": plan, "texts_in_image": textplan.plan_texts(plan), "prompt_original": texto,
            "parent_job_id": row["id"]}
    if record:
        spec["pedido_ajuste"] = record
    spec.pop("job_id", None)
    started = now()
    asset_id, report = apply_texts(user_id, asset_bytes(art), spec)
    note = size_feedback(previous_spec.get("text_plan"), plan, (result.get("validacao_texto") or {}), report or {})
    job_id, ts = new_id(), now()
    payload = {"version": 2, "gen_type": "text_edit", "size": (loads(row["payload"], {}) or {}).get("size"), "spec": spec}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, progress, payload, status_message, result, "
        "schema_version, gen_type, prompt_original, prompt_visual, style, aspect_ratio, seed, render_mode, parent_job_id, "
        "timings, created_at, updated_at, started_at, finished_at) "
        "VALUES (?, ?, ?, 'render', 'done', 100, ?, 'Pronto', ?, 2, 'text_edit', ?, ?, ?, ?, ?, 'texto', ?, ?, ?, ?, ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload),
         dumps({"asset_id": asset_id, "art_asset_id": art_id, "validacao_texto": report}), texto,
         spec.get("prompt_visual"), spec.get("style"), spec.get("aspect_ratio"), spec.get("seed"), row["id"],
         dumps({"texto_s": round(ts - started, 2)}), ts, ts, started, ts),
    )
    save_spec(conv["id"], {**spec, "job_id": job_id})
    if note:
        g.text_edit_note = note
    if record:
        schedule_evaluation(get_conn().one("SELECT * FROM sg_jobs WHERE id = ?", (job_id,)),
                            {"asset_id": asset_id})
    return job_id


def recolor_art(data, colors):
    """Troca a paleta preservando formas: mapa de gradiente pela luminosidade (escuro -> claro) nas cores pedidas.
    Mantém o contraste e os detalhes; a transparência e a composição ficam idênticas."""
    from PIL import Image, ImageOps  # noqa: PLC0415

    img = Image.open(io.BytesIO(data)).convert("RGB")
    cols = sorted([tuple(c) for c in colors], key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    darkest = tuple(int(v * 0.18) for v in cols[0])
    lightest = tuple(int(v + (255 - v) * 0.3) for v in cols[-1])  # o claro continua sendo a cor pedida (não bege)
    stops = [darkest] + cols + [lightest]
    lut = []
    for ch in range(3):
        for i in range(256):
            t = i / 255 * (len(stops) - 1)
            k = min(int(t), len(stops) - 2)
            f = t - k
            lut.append(int(stops[k][ch] + (stops[k + 1][ch] - stops[k][ch]) * f))
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    mapped = Image.merge("RGB", [gray.point(lut[c * 256:(c + 1) * 256]) for c in range(3)])
    out = io.BytesIO()
    mapped.save(out, "PNG")
    return out.getvalue()


def _rel_luminance(c):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g_, b = (ch(x) for x in c[:3])
    return 0.2126 * r + 0.7152 * g_ + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = sorted((_rel_luminance(a), _rel_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def legible_text_color(art_bytes, palette):
    """Cor do texto depois de trocar a paleta: a mais viva que tenha contraste >= 3:1 com o fundo onde o texto fica
    (centro da peça); se nenhuma tiver, a versão clara/escura da paleta com mais contraste (nunca ilegível)."""
    from PIL import Image, ImageStat  # noqa: PLC0415

    img = Image.open(io.BytesIO(art_bytes)).convert("RGB")
    w, h = img.size
    region = img.crop((int(w * .15), int(h * .25), int(w * .85), int(h * .75))).resize((64, 64))
    bg = tuple(int(v) for v in ImageStat.Stat(region).median)
    vivid = sorted(palette, key=lambda c: -(max(c) - min(c)))
    for c in vivid:
        if contrast_ratio(c, bg) >= 3.0:
            return [int(v) for v in c]
    light = [tuple(min(255, int(v + (255 - v) * .85)) for v in c) for c in palette] + [(250, 250, 250)]
    dark = [tuple(int(v * .18) for v in c) for c in palette] + [(18, 18, 22)]
    best = max(light + dark, key=lambda c: contrast_ratio(c, bg))
    return [int(v) for v in best]


def recolor_job(conv, user_id, texto, previous_spec, spec):
    """“Recolorir”: nova arte a partir da anterior (sem gerador), textos reaplicados e conferência visual."""
    row = last_result_row(conv["id"])
    result = (loads(row["result"], {}) or {}) if row else {}
    art = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (result.get("art_asset_id"), user_id))
    if not art:
        return None
    new_art = sanitize_image(recolor_art(asset_bytes(art), spec["cores"]), 16 * 1024 * 1024, 40_000_000, 4096)
    art_id = store_asset(user_id, "art", *new_art)
    plan = dict(spec.get("text_plan") or previous_spec.get("text_plan") or {}) or None
    if plan:  # o texto acompanha a paleta nova, mas só com contraste legível sobre a região onde ele fica
        plan["cor"] = legible_text_color(new_art[0], [tuple(c) for c in spec["cores"]])
    full = {**previous_spec, "text_plan": plan,
            "pedido_ajuste": spec.get("pedido_ajuste"), "prompt_original": texto, "parent_job_id": row["id"]}
    full.pop("job_id", None)
    full.pop("recompor", None)
    if full.get("text_plan"):
        asset_id, report = apply_texts(user_id, new_art[0], full)
    else:
        asset_id, report = store_asset(user_id, "result", *new_art), {}
    job_id, ts = new_id(), now()
    payload = {"version": 2, "gen_type": "art_edit", "size": (loads(row["payload"], {}) or {}).get("size"), "spec": full}
    get_conn().run(
        "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, progress, payload, status_message, result, "
        "schema_version, gen_type, prompt_original, prompt_visual, style, aspect_ratio, seed, render_mode, parent_job_id, "
        "created_at, updated_at, started_at, finished_at) "
        "VALUES (?, ?, ?, 'render', 'done', 100, ?, 'Pronto', ?, 2, 'art_edit', ?, ?, ?, ?, ?, 'cores', ?, ?, ?, ?, ?)",
        (job_id, user_id, conv["id"], dumps(payload), dumps({"asset_id": asset_id, "art_asset_id": art_id,
                                                              "validacao_texto": report}),
         texto, full.get("prompt_visual"), full.get("style"), full.get("aspect_ratio"), full.get("seed"), row["id"],
         ts, ts, ts, ts),
    )
    save_spec(conv["id"], {**full, "job_id": job_id})
    schedule_evaluation(get_conn().one("SELECT * FROM sg_jobs WHERE id = ?", (job_id,)), {"asset_id": asset_id})
    return job_id


def size_feedback(old_plan, new_plan, old_report, new_report):
    """Pedido de texto maior/menor que não mudou de verdade (já no limite): avisa o cliente com sinceridade."""
    before, after = old_report.get("tamanhos") or {}, new_report.get("tamanhos") or {}
    old_scale = {b["texto"]: float(b.get("escala", 1.0)) for b in (old_plan or {}).get("blocos") or []}
    for b in new_plan.get("blocos") or []:
        wanted = float(b.get("escala", 1.0)) / old_scale.get(b["texto"], float(b.get("escala", 1.0)))
        if b["texto"] in before and b["texto"] in after and abs(wanted - 1) > 0.05:
            grew = after[b["texto"]] / max(1, before[b["texto"]])
            if wanted > 1 and grew < 1.05:
                return (f"“{b['texto']}” já está no maior tamanho que cabe na arte sem cortar. "
                        "Se quiser, posso deixá-lo em uma linha só, encurtar o texto ou diminuir os outros textos.")
    return None


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
        note = resposta if resposta == creative.REDESIGN_REPLY else \
            "Entendi que é um encarte com produtos e preços: preencha e confira o briefing ao lado."
        interpret_with_rules(conv["id"], texto, previous, message_id, insert=insert, note=note)
        return None
    if acao == "ajustar_texto":
        # Ajuste só de texto (interpretado pelo modelo): mesma arte, textos refeitos na hora.
        problem = render_quota_problem(g.user_id)
        previous_spec = loads(conv.get("spec"), None) or {}
        job_id = None if problem else text_edit_job(conv, g.user_id, texto, previous_spec, spec["text_plan"],
                                                    spec.get("pedido_ajuste"))
        if not job_id and not problem:
            resposta = "Não consegui ajustar o texto desta vez. Pode me dizer de novo o que quer mudar?"
        elif getattr(g, "text_edit_note", None):
            resposta = g.text_edit_note
        if insert:
            add_message(conv["id"], "assistant", problem or resposta, message_id=message_id, job_id=job_id)
        else:
            set_message(message_id, conv["id"], problem or resposta, job_id)
        return job_id
    if acao == "recolorir":
        problem = render_quota_problem(g.user_id)
        job_id = None if problem else recolor_job(conv, g.user_id, texto, loads(conv.get("spec"), None) or {}, spec)
        text = problem or (resposta if job_id else "Não consegui trocar as cores desta vez. Pode me dizer de novo?")
        if insert:
            add_message(conv["id"], "assistant", text, message_id=message_id, job_id=job_id)
        else:
            set_message(message_id, conv["id"], text, job_id)
        return job_id
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
    if creative.wants_commercial_redesign(texto, references):
        # Redesign de arte comercial: nunca por geração (trocaria os produtos). Lê produtos, preços e fotos da arte
        # (Acer) e monta o encarte com os dados reais; sem o leitor disponível, pede os dados ao cliente.
        reply, job_id = enqueue_reference_read(conv, g.user_id, texto, references[0])
        if job_id:
            add_message(conv["id"], "assistant", reply, message_id=assistant_id, job_id=job_id)
        else:
            apply_creative_decision(conv, "encarte", creative.REDESIGN_REPLY, None, assistant_id, True, texto)
        return jsonify({"ok": True}), 201
    if creative.wants_reference_edit(texto, references):
        reply, job_id = enqueue_reference_edit(conv, g.user_id, texto, references[0])
        add_message(conv["id"], "assistant", reply, message_id=assistant_id, job_id=job_id)
        return jsonify({"ok": True}), 201
    if previous_result and previous_spec and previous_spec.get("pedido_ajuste") and edits.COMPLAINT.search(texto) \
            and not references and not interpreter_available():
        retried = creative.retry_last_adjustment(previous_spec, texto, previous_result, conversation_texts(conv["id"]),
                                                 options)
        if retried:
            apply_creative_decision(conv, retried[0], retried[1], retried[2], assistant_id, True, texto)
            return jsonify({"ok": True}), 201
    if previous_spec and previous_spec.get("pending") and previous_spec.get("texto_pedido"):
        # Resposta à pergunta "qual frase você quer?": resolvida aqui, sem o modelo (texto literal do usuário).
        spec = creative.resolve_text_answer(texto, previous_spec)
        if spec:
            apply_creative_decision(conv, "gerar", "Perfeito! Vou criar a arte com esse texto.", spec, assistant_id, True, texto)
            return jsonify({"ok": True}), 201
    if previous_result and not references and not interpreter_available() and previous_spec             and (previous_spec.get("text_plan") or {}).get("blocos") and not creative.NEW_REQUEST.search(texto):
        # Sem o modelo: o mesmo planejador de ajustes (textos conferidos; cena só se o pedido falar dela).
        decision = edits.plan_adjustment(previous_spec, texto, {}, conversation_texts(conv["id"]))
        if decision["feitas"] or decision["cena"]:
            acao, resposta, spec = creative.adjustment_outcome(decision, previous_spec, previous_result, "", {}, options,
                                                               texto, conversation_texts(conv["id"]))
            apply_creative_decision(conv, acao, resposta, spec, assistant_id, True, texto)
            return jsonify({"ok": True}), 201
    if previous_result and not references and not interpreter_available() and is_text_only_edit(texto, previous_spec):
        problem = render_quota_problem(g.user_id)
        job_id = None if problem else text_edit_job(conv, g.user_id, texto, previous_spec)
        if job_id or problem:
            add_message(conv["id"], "assistant", problem or "Pronto! Ajustei o texto na mesma arte.", job_id=job_id)
            return jsonify({"ok": True}), 201
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
            "current_image_id": last_final_asset(conv["id"]) if previous_result else None,
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
    previous_spec = payload.get("previous_spec") or {}
    if (isinstance(model_answer, dict) and model_answer.get("tipo_pedido") == "nova" and payload.get("previous_result_id")
            and edits.refers_to_current(payload.get("text", ""), previous_spec.get("text_plan"))):
        # O modelo disse "nova", mas a mensagem só fala da peça atual: trata como ajuste (sem apagar o que existe).
        current_app.logger.info("guarda: pedido tratado como ajuste (modelo disse 'nova')")
        kind = edits.classify(payload.get("text", ""), previous_spec.get("text_plan"))
        model_answer = {**model_answer, "tipo_pedido": "ajuste", "editar_anterior": kind["cena"],
                        "prompt_visual": model_answer.get("prompt_visual") or previous_spec.get("prompt_visual"),
                        "ajuste": {"cena": kind["cena"], "textos": kind["texto"], "operacoes_texto": []}}
    acao, resposta, spec = creative.normalize_model_answer(
        model_answer, payload.get("text", ""), payload.get("options"), payload.get("previous_spec"),
        payload.get("previous_result_id"), references, conversation_texts(conv["id"]),
    )
    if acao == "gerar" and isinstance(spec, dict) and spec.get("recompor") and \
            not subject_separable(conv["user_id"], spec["recompor"]["sujeito"]):
        # O elemento principal não se separa do fundo atual: refaz o visual com liberdade (mesma intenção).
        plano = {**spec["pedido_ajuste"]["plano"], "estrategia": "reestilizar"}
        acao, resposta, spec = creative.plan_outcome(
            plano, {**(model_answer or {}), "prompt_visual": spec["recompor"]["prompt_completo"]}, payload.get("text", ""),
            payload.get("options"), payload.get("previous_spec"), payload.get("previous_result_id"),
            conversation_texts(conv["id"]), composite=False)
    apply_creative_decision(conv, acao, resposta, spec, payload["assistant_message_id"], False, payload.get("text", ""))


def subject_separable(user_id, asset_id):
    from . import composer  # noqa: PLC0415

    row = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (asset_id, user_id))
    if not row:
        return False
    try:
        return composer.cutout(typography.Image.open(typography.io.BytesIO(asset_bytes(row))))[1]
    except Exception:  # noqa: BLE001
        return False


def recompose_subject(user_id, spec, background):
    """trocar_fundo: o elemento principal da arte anterior (recortado) sobre o fundo novo, na mesma posição e escala."""
    from PIL import Image  # noqa: PLC0415

    from . import composer  # noqa: PLC0415

    row = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", ((spec.get("recompor") or {}).get("sujeito"),
                                                                                   user_id))
    if not row:
        return None
    old = Image.open(io.BytesIO(asset_bytes(row)))
    subject, ok = composer.cutout(old)
    box = subject.info.get("bbox")
    if not ok or not box:
        return None
    bg = Image.open(io.BytesIO(background)).convert("RGBA")
    sx, sy = bg.width / old.width, bg.height / old.height
    w, h = int((box[2] - box[0]) * sx), int((box[3] - box[1]) * sy)
    piece = subject.resize((max(1, w), max(1, h)), Image.LANCZOS)
    composer.shadowed_paste(bg, piece, (int(box[0] * sx), int(box[1] * sy)), blur=12, offset=(0, 10), alpha=90)
    out = io.BytesIO()
    bg.convert("RGB").save(out, "PNG")
    return out.getvalue()


def creative_fallback(payload, note=""):
    conv, references = _creative_context(payload)
    texto = payload.get("text", "")
    previous_spec = loads(conv.get("spec"), None)
    if payload.get("previous_result_id") and not references and is_text_only_edit(texto, previous_spec):
        apply_creative_decision(conv, "ajustar_texto", "Pronto! Ajustei o texto na mesma arte.",
                                {"text_plan": edits.revise_plan(previous_spec["text_plan"], texto)},
                                payload["assistant_message_id"], False, texto)
        return
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
    data = data if isinstance(data, dict) else {}
    previous = loads(conv["briefing"]) or {}
    for key in ("layout", "confirmar", "gerado"):  # o formulário não edita esses campos: preserva os da conversa
        data.setdefault(key, previous.get(key))
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
    if not (briefing.get("layout") or {}).get("fundo_ia"):
        # Layout escolhido pela conversa: composição imediata no servidor (mesmo resultado do chat).
        text, job_id = generate_flyer(conv, g.user_id, briefing)
        if text:
            add_message(conv["id"], "assistant", text, job_id=job_id)
        if not job_id:
            return jsonify({"ok": True, "job": None, "mensagem": text or "Criando o seu encarte; acompanhe na conversa."}), 201
        return jsonify({"ok": True, "job": job_view(conn.one("SELECT * FROM sg_jobs WHERE id = ?", (job_id,)))}), 201

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
    if not job or job.get("gen_type") not in ("free_image", "text_edit"):
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


def text_check_summary(result):
    report = result.get("validacao_texto")
    if not report:
        return None
    failed = [c["nome"] for c in report.get("verificacoes", []) if not c.get("ok")]
    ocr = report.get("ocr") or {}
    return {"ok": report.get("ok"), "falhas": failed, "correcoes": len(report.get("correcoes") or []),
            "tentativas": report.get("tentativas"), "composto_no": result.get("composto_no"),
            "superficie": (report.get("superficie") or {}).get("modo"),
            "leitura_visual": None if not ocr.get("textos") and ocr.get("disponivel") is False else ocr.get("ok"),
            "leitura_s": ocr.get("tempo_s"), "indesejados": [e.get("texto") for e in ocr.get("indesejados") or []][:5]}


@bp.get("/api/admin/jobs")
@admin_required
def admin_jobs():
    """Atividade recente com detalhes técnicos (só no painel do administrador)."""
    rows = get_conn().all(
        "SELECT j.id, j.kind, j.gen_type, j.status, j.status_message, j.error, j.payload, j.timings, j.worker_id, "
        "j.render_mode, j.result, j.created_at, j.started_at, j.finished_at, u.name AS user_name "
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
            "textos": text_check_summary(loads(r["result"], {}) or {}),
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
    body = request.get_json(silent=True) or {}
    capable = "reference_edit" in (body.get("capacidades") or [])
    if capable:
        touch_worker(f"{g.worker_id}:refedit", "refedit")
    if "tools" in (body.get("capacidades") or []):
        touch_worker(f"{g.worker_id}:tools", "tools")
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
        "SELECT id FROM sg_jobs WHERE kind = ? AND status = 'queued' AND (gen_type IS NULL OR gen_type NOT IN "
        "('reference_edit', 'reference_read') OR ? = 1) AND (? = 0 OR gen_type IN ('reference_edit', 'reference_read')) "
        "AND (gen_type IS NULL OR gen_type NOT IN ('layout_ref', 'websearch', 'flyer_ai') OR ? = 1) "
        "AND (? = 0 OR gen_type IN ('layout_ref', 'websearch', 'flyer_ai')) "
        # Mensagens do cliente antes das conferências (a conversa nunca espera uma avaliação).
        "ORDER BY CASE WHEN gen_type = 'evaluate' THEN 1 ELSE 0 END, created_at LIMIT 5",
        (kind, 1 if capable else 0, 1 if set(body.get("somente") or []) & {"reference_edit", "reference_read"} else 0,
         1 if "tools" in (body.get("capacidades") or []) else 0, 1 if "tools" in (body.get("somente") or []) else 0)
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


@bp.get("/worker/assets/<asset_id>/preview")
@worker_required
def worker_asset_preview(asset_id):
    """Mesma regra de acesso de worker_asset, em tamanho reduzido (lado maior 768 px) para a análise visual."""
    resp = _worker_asset_response(asset_id)
    from PIL import Image  # noqa: PLC0415

    img = Image.open(io.BytesIO(resp.get_data())).convert("RGB")
    img.thumbnail((768, 768), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return Response(out.getvalue(), mimetype="image/jpeg")


@bp.get("/worker/assets/<asset_id>")
@worker_required
def worker_asset(asset_id):
    return _worker_asset_response(asset_id)


def _worker_asset_response(asset_id):
    """Imagem de uma tarefa em andamento deste worker (só os anexos da própria tarefa)."""
    asset_id = valid_id(asset_id)
    jobs = get_conn().all(
        "SELECT payload, user_id FROM sg_jobs WHERE worker_id = ? AND status IN ('claimed', 'running')", (g.worker_id,)
    )
    for job in jobs:
        payload = loads(job["payload"], {})
        allowed = set(payload.get("asset_ids") or []) | set(payload.get("image_asset_ids") or [])
        allowed |= {payload.get(k) for k in ("current_image_id", "before_id", "after_id", "asset_id") if payload.get(k)}
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
        spec = (loads(job["payload"], {}) or {}).get("spec") or {}
        result = {}
        recomposed = recompose_subject(job["user_id"], spec, data) if spec.get("recompor") else None
        if recomposed:
            data = recomposed
        if spec.get("encarte"):
            # Fundo do encarte pronto: monta o encarte (fotos, nomes e preços do briefing) sobre ele.
            art_id = store_asset(job["user_id"], "art", data, mime, width, height)
            flyer = layouts.render(spec["encarte"], _flyer_assets(job["user_id"], spec["encarte"]), scene_bytes=data)
            with typography.Image.open(typography.io.BytesIO(flyer)) as img:
                fw, fh = img.size
            asset_id = store_asset(job["user_id"], "result", flyer, "image/jpeg", fw, fh)
            result = {"asset_id": asset_id, "art_asset_id": art_id, "encarte": True}
        elif spec.get("text_plan"):
            art_id = store_asset(job["user_id"], "art", data, mime, width, height)
            started = now()
            try:
                asset_id, report = apply_texts(job["user_id"], data, spec)
            except Exception as exc:  # noqa: BLE001 — sem os textos, a peça não é entregue como pronta
                current_app.logger.exception("composição de texto falhou no job %s", job["id"])
                conn.run("UPDATE sg_jobs SET status = 'failed', error = ?, status_message = 'Falhou', updated_at = ? "
                         "WHERE id = ?", (f"tipografia: {type(exc).__name__}", now(), job["id"]))
                return jsonify({"ok": True, "asset_id": None})
            timings = loads(job.get("timings"), {}) or {}
            timings["texto_s"] = round(now() - started, 2)
            conn.run("UPDATE sg_jobs SET timings = ? WHERE id = ?", (dumps(timings), job["id"]))
            result = {"asset_id": asset_id, "art_asset_id": art_id, "validacao_texto": report, "composto_no": "servidor"}
            surface_note(job, report)
            result["conferencia"] = confirm_adjustment(job, report, data)
        else:
            asset_id = store_asset(job["user_id"], "result", data, mime, width, height)
            result = {"asset_id": asset_id}
            result["conferencia"] = confirm_adjustment(job, None, data)
        conn.run(
            "UPDATE sg_jobs SET status = 'done', progress = 100, status_message = 'Pronto', result = ?, finished_at = ?, "
            "updated_at = ? WHERE id = ?",
            (dumps(result), now(), now(), job["id"]),
        )
        schedule_evaluation(job, result)
        return jsonify({"ok": True, "asset_id": asset_id})

    # Interpretação: nunca confiar no modelo, tudo passa pela validação.
    data = request.get_json(silent=True) or {}
    payload = loads(job["payload"], {})
    if payload.get("mode") in ("layout_ref", "websearch", "flyer_design"):
        touch_worker(f"{g.worker_id}:tools", "tools")  # acabou de entregar: continua disponível para a próxima etapa
    if payload.get("mode") == "flyer_design":
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ?, result = ? WHERE id = ?",
                 (now(), now(), dumps({"nota": data.get("nota"), "relatorio": data.get("relatorio")})[:8000], job["id"]))
        handle_flyer_design(job, payload, data)
        return jsonify({"ok": True})
    if payload.get("mode") == "websearch":
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ?, result = ? WHERE id = ?",
                 (now(), now(), dumps({"notas": data.get("notas")})[:8000], job["id"]))
        handle_websearch(job, payload, data)
        return jsonify({"ok": True})
    if payload.get("mode") == "layout_ref":
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ?, result = ? WHERE id = ?",
                 (now(), now(), dumps({"relatorio": data.get("relatorio")})[:8000], job["id"]))
        handle_layout_ref(job, payload, data)
        return jsonify({"ok": True})
    if payload.get("mode") == "evaluate":
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ?, result = ? WHERE id = ?",
                 (now(), now(), dumps({"avaliacao": data.get("avaliacao")})[:8000], job["id"]))
        handle_evaluation(job, payload, data.get("avaliacao"))
        return jsonify({"ok": True})
    if payload.get("mode") == "creative":
        g.user_id = job["user_id"]
        conn.run("UPDATE sg_jobs SET status = 'done', progress = 100, finished_at = ?, updated_at = ? WHERE id = ?",
                 (now(), now(), job["id"]))
        model_answer = data.get("modelo")
        conn.run("UPDATE sg_jobs SET result = ? WHERE id = ?",
                 (dumps({"modelo": model_answer})[:20000] if isinstance(model_answer, dict) else None, job["id"]))
        creative_from_model(payload, model_answer)
        return jsonify({"ok": True})
    conversation_id = payload["conversation_id"]
    previous = payload.get("previous_briefing") or {}
    proposed = merge_previous_assets(data.get("briefing") if isinstance(data.get("briefing"), dict) else {}, previous)
    # Uma só fonte de verdade: o que o cliente já informou (painel/conversa) nunca some porque o modelo esqueceu.
    last_user = next((m["content"] for m in reversed(payload.get("history") or []) if m["role"] == "user"), "")
    proposed = keep_known_products(proposed, bf.interpret_rules(last_user, previous))
    texts = user_texts(conversation_id) + confirmed_prices(previous)
    ids = briefing_asset_ids(proposed)
    owned = set()
    if ids:
        marks = ",".join("?" for _ in ids)
        owned = {r["id"] for r in conn.all(f"SELECT id FROM sg_assets WHERE user_id = ? AND id IN ({marks})", [job["user_id"], *ids])}
    normalized = bf.normalize_briefing(proposed, texts, owned)
    resposta = bf._clean_text(data.get("resposta"), 1500) or "Atualizei o briefing."
    if normalized_ready_except_photos(normalized) and OPTIONAL_FIELD.search(resposta):
        # Campos opcionais (loja, validade, formato…) não são cobrados: o Studio decide o visual se o cliente não disser.
        resposta = drop_optional_questions(resposta) or (
            "Montei o encarte com os produtos e preços que você passou. Loja e validade são opcionais.")
    if normalized["pendencias"]:
        resposta += "\n\nFalta:\n" + "\n".join(f"– {p}" for p in normalized["pendencias"][:6])
    # Outras tarefas podem ter atualizado o briefing enquanto o modelo pensava (análise da referência, opções e fotos
    # escolhidas): esses campos vêm do estado ATUAL, não da cópia enviada ao modelo.
    current = loads(conn.one("SELECT briefing FROM sg_conversations WHERE id = ?", (conversation_id,))["briefing"]) or {}
    for key in ("layout", "opcoes_foto", "referencias", "encarte_id"):
        if current.get(key):
            normalized[key] = current[key]
    photos = {p["nome"]: p.get("asset_id") for p in current.get("produtos") or [] if p.get("asset_id")}
    for prod in normalized["produtos"]:
        if not prod.get("asset_id") and photos.get(prod["nome"]):
            prod["asset_id"] = photos[prod["nome"]]
    normalized = bf.normalize_briefing(normalized, owned_asset_ids=owned_asset_ids(job["user_id"], briefing_asset_ids(normalized)),
                                       trust_prices=True)
    if isinstance(data.get("layout"), str) and data["layout"].strip():
        normalized["layout"] = {**(normalized.get("layout") or {}), "instrucoes": bf._clean_text(data["layout"], 300)}
    save_briefing(conversation_id, normalized)
    resposta = CLAIM_SENTENCE.sub("", resposta).strip() or "Anotei."
    job_id = None
    if data.get("gerar") is True or flyerchat.wants_generation(last_user):
        g.user_id = job["user_id"]
        conv_row = conn.one("SELECT * FROM sg_conversations WHERE id = ?", (conversation_id,))
        text, job_id = generate_flyer(conv_row, job["user_id"], normalized)
        if text:
            resposta = text if not normalized["pendencias"] or job_id else f"{resposta}\n\n{text}"
    conn.run(
        "UPDATE sg_messages SET content = ?, status = 'ok', job_id = ? WHERE id = ? AND conversation_id = ?",
        (resposta, job_id, payload["assistant_message_id"], conversation_id),
    )
    conn.run(
        "UPDATE sg_jobs SET status = 'done', progress = 100, updated_at = ? WHERE id = ?", (now(), job["id"])
    )
    return jsonify({"ok": True})


@bp.post("/worker/jobs/<job_id>/complete-composed")
@worker_required
def worker_complete_composed(job_id):
    """Agente v2.3: arte limpa + peça final já com os textos (validada no Acer, inclusive com leitura visual)."""
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415

    job = worker_job(job_id)
    if job["kind"] != "render":
        return error("Tarefa inválida.")
    data = request.get_json(silent=True) or {}
    try:
        art = sanitize_image(base64.b64decode(data.get("arte") or ""), 8 * 1024 * 1024, 40_000_000, 4096)
        final = sanitize_image(base64.b64decode(data.get("final") or ""), 8 * 1024 * 1024, 40_000_000, 4096)
    except (ImageRejected, binascii.Error, ValueError) as exc:
        return error(str(exc) or "imagem inválida")
    report = data.get("relatorio") if isinstance(data.get("relatorio"), dict) else {}
    if len(dumps(report)) > 60_000:
        report = {k: report.get(k) for k in ("ok", "tentativas", "correcoes", "superficie", "ocr") if k in report}
    conn = get_conn()
    spec = (loads(job["payload"], {}) or {}).get("spec") or {}
    recomposed = recompose_subject(job["user_id"], spec, art[0]) if spec.get("recompor") else None
    if recomposed:
        # Troca de fundo: o Acer gerou só o fundo; o elemento principal e os textos são aplicados aqui.
        art = sanitize_image(recomposed, 16 * 1024 * 1024, 40_000_000, 4096)
        art_id = store_asset(job["user_id"], "art", *art)
        asset_id, report = apply_texts(job["user_id"], art[0], spec) if spec.get("text_plan") else \
            (store_asset(job["user_id"], "result", *art), {})
        composed = "servidor"
    else:
        art_id = store_asset(job["user_id"], "art", *art)
        asset_id = store_asset(job["user_id"], "result", *final)
        composed = "acer"
    result = {"asset_id": asset_id, "art_asset_id": art_id, "validacao_texto": report, "composto_no": composed}
    result["conferencia"] = confirm_adjustment(job, report, art[0])
    conn.run(
        "UPDATE sg_jobs SET status = 'done', progress = 100, status_message = 'Pronto', result = ?, finished_at = ?, "
        "updated_at = ? WHERE id = ?",
        (dumps(result), now(), now(), job["id"]),
    )
    surface_note(job, report)
    schedule_evaluation(job, result)
    return jsonify({"ok": True, "asset_id": asset_id})


FAVICON_WANTED = re.compile(r"favicon|[íi]cone d[oae]s? (site|app|aplicativo|loja|navegador)|como [íi]cone|"
                            r"usar (tamb[ée]m )?(como|de) [íi]cone", re.IGNORECASE)


def favicon_kit(user_id, art_bytes, plan):
    """Versões para tamanhos pequenos: só o símbolo (recortado) num quadrado da cor da marca; sem símbolo
    separável, as iniciais da marca. Devolve (folha de prévia, [ids dos PNGs 512/192/48/32/16])."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from . import composer  # noqa: PLC0415

    arts = [art_bytes] if isinstance(art_bytes, (bytes, bytearray)) else list(art_bytes)
    art = Image.open(io.BytesIO(arts[0])).convert("RGB")
    subject, ok = None, False
    # 1º: arte em que o símbolo se separa de forma limpa (fundo uniforme); 2º: símbolo colorido sobre fundo claro.
    for finder in (composer.cutout, _saturated_symbol):
        for data in arts:
            subject, ok = finder(Image.open(io.BytesIO(data)).convert("RGB"))
            if ok:
                break
        if ok:
            break
    brand = _brand_color(subject if ok else art)
    if ok:
        bg = tuple(int(v + (255 - v) * 0.88) for v in brand)  # fundo claro na cor da marca, símbolo em destaque
    else:
        bg = brand
    base = Image.new("RGBA", (512, 512), bg + (255,))
    small_base = None
    if ok and subject.width > 20:
        piece = subject.copy()
        piece.thumbnail((400, 400), Image.LANCZOS)
        small_base = base.copy()
        base.alpha_composite(piece, ((512 - piece.width) // 2, (512 - piece.height) // 2))
        # Em 16-48 px o traço fino some: versão com o símbolo maior e o traço engrossado.
        from PIL import ImageFilter  # noqa: PLC0415

        bold = subject.copy()
        bold.thumbnail((460, 460), Image.LANCZOS)
        alpha = bold.getchannel("A").filter(ImageFilter.MaxFilter(7))  # engrossa sem virar mancha
        solid = Image.new("RGBA", bold.size, _brand_color(subject) + (255,))
        solid.putalpha(alpha)
        small_base.alpha_composite(solid, ((512 - bold.width) // 2, (512 - bold.height) // 2))
    else:
        words = [w for w in " ".join(textplan.plan_texts(plan)).split() if w[:1].isalnum()] or ["S"]
        initials = "".join(w[0] for w in words[:2]).upper()
        light = sum(bg) / 3 > 140
        family = {"tecnologico": "orbitron", "futurista": "orbitron"}.get((plan or {}).get("estilo"), "montserrat")
        f = typography.get_font(family, 800, 300 if len(initials) == 1 else 230)
        ImageDraw.Draw(base).text((256, 260), initials, font=f, fill=(20, 20, 24) if light else (250, 250, 250), anchor="mm")
    mask = Image.new("L", (512, 512), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 511, 511), radius=96, fill=255)
    icon = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)
    icon_small = icon
    if small_base is not None:
        icon_small = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        icon_small.paste(small_base, (0, 0), mask)
    ids, previews = [], []
    for size in (512, 192, 48, 32, 16):
        im = (icon if size > 48 else icon_small).resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        ids.append(store_asset(user_id, "result", buf.getvalue(), "image/png", size, size))
        previews.append(im)
    sheet = Image.new("RGB", (860, 320), (245, 245, 247))
    d = ImageDraw.Draw(sheet)
    x = 30
    for size, im in zip((512, 192, 48, 32, 16), previews):
        show = im.resize((256, 256), Image.LANCZOS) if size == 512 else im.resize((160, 160), Image.LANCZOS) \
            if size == 192 else im  # 48/32/16 em tamanho real: é assim que aparecem na aba do navegador
        sheet.paste(show, (x, 160 - show.height // 2), show)
        d.text((x, 290), f"{size}px", fill=(60, 60, 66), font=typography.get_font("poppins", 600, 20))
        x += show.width + 40
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=90)
    return store_asset(user_id, "preview", buf.getvalue(), "image/jpeg", sheet.width, sheet.height), ids


def _saturated_symbol(img):
    """Símbolo de traço colorido sobre fundo claro/degradê: os pixels de cor forte (saturados) formam o símbolo."""
    from PIL import Image  # noqa: PLC0415

    hsv = img.convert("HSV")
    s_ch, v_ch = hsv.getchannel("S"), hsv.getchannel("V")
    mask = Image.eval(s_ch, lambda v: 255 if v > 110 else 0)
    mask = Image.composite(mask, Image.new("L", img.size, 0), Image.eval(v_ch, lambda v: 255 if v > 40 else 0))
    bbox = mask.getbbox()
    if not bbox:
        return img, False
    area = sum(1 for v in mask.resize((96, 96)).getdata() if v > 128) / (96 * 96)
    if not 0.01 < area < 0.45:
        return img, False
    out = img.convert("RGBA")
    out.putalpha(mask)
    piece = out.crop(bbox)
    return piece, True


def _brand_color(img):
    """Cor dominante mais viva (a "cor da marca") da imagem; ignora fundo transparente, branco e cinza."""
    small = img.convert("RGBA").resize((64, 64))
    counts = {}
    for r, g_, b, a in small.getdata():
        if a < 128 or max(r, g_, b) - min(r, g_, b) < 50:
            continue
        key = (r // 32 * 32 + 16, g_ // 32 * 32 + 16, b // 32 * 32 + 16)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return (30, 60, 120)
    return max(counts, key=counts.get)


def maybe_favicon(job, result):
    """Pedido de logo para usar como ícone/favicon: entrega também as versões pequenas."""
    try:
        texts = " ".join(conversation_texts(job["conversation_id"]))
        if not FAVICON_WANTED.search(texts) or not result.get("art_asset_id"):
            return
        rows = get_conn().all("SELECT result FROM sg_jobs WHERE conversation_id = ? AND kind = 'render' AND status = 'done' "
                              "ORDER BY created_at DESC LIMIT 6", (job["conversation_id"],))
        ids_art = [result["art_asset_id"]] + [(loads(r["result"], {}) or {}).get("art_asset_id") for r in rows]
        arts = []
        for aid in dict.fromkeys(a for a in ids_art if a):
            row = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (aid, job["user_id"]))
            if row:
                arts.append(asset_bytes(row))
        spec = (loads(job["payload"], {}) or {}).get("spec") or {}
        sheet, ids = favicon_kit(job["user_id"], arts, spec.get("text_plan"))
        add_message(job["conversation_id"], "assistant",
                    "Para o ícone do site, fiz versões simplificadas (só o símbolo, sem texto, que fica ilegível em "
                    "16–32 px), nos tamanhos 512, 192, 48, 32 e 16 px. Os de 48, 32 e 16 aparecem em tamanho real na folha.",
                    [{"id": sheet, "kind": "preview"}] + [{"id": i, "kind": "favicon"} for i in ids])
    except Exception:  # noqa: BLE001 — o kit nunca impede a entrega da arte
        current_app.logger.exception("kit de favicon falhou no job %s", job.get("id"))


# Conferência visual depois da entrega ("Conferi o resultado…"). Desligada por decisão do produto (03/11/2026);
# SG_POST_EVALUATION=1 religa sem mudar código.
POST_EVALUATION = os.environ.get("SG_POST_EVALUATION") == "1"


def schedule_evaluation(job, result):
    """Depois de gerar: o modelo de visão compara antes x depois x pedido. Até o veredito, nada é dado como conferido."""
    if (loads(job["payload"], {}) or {}).get("gen_type") in ("free_image", "text_edit", "art_edit") and \
            job.get("conversation_id"):
        maybe_favicon(job, result)
    if not POST_EVALUATION:  # decisão do produto: sem conferência depois de entregue (a qualidade é cuidada antes)
        return
    try:
        payload_job = loads(job["payload"], {}) or {}
        spec = payload_job.get("spec") or {}
        if payload_job.get("gen_type") not in ("free_image", "text_edit", "art_edit") or spec.get("encarte") or \
                not job.get("conversation_id"):
            return
        if not interpreter_available() or not result.get("asset_id"):
            return
        record = spec.get("pedido_ajuste") or {}
        before = None
        if job.get("parent_job_id") or spec.get("parent_job_id"):
            parent = get_conn().one("SELECT result FROM sg_jobs WHERE id = ?", (job.get("parent_job_id") or spec.get("parent_job_id"),))
            before = (loads(parent["result"], {}) or {}).get("asset_id") if parent else None
        msg_id = new_id()
        add_message(job["conversation_id"], "assistant", "Conferindo se o resultado ficou como você pediu…",
                    status="pending", message_id=msg_id)
        eval_payload = {
            "mode": "evaluate", "conversation_id": job["conversation_id"], "assistant_message_id": msg_id,
            "render_job_id": job["id"], "before_id": before if record else None, "after_id": result["asset_id"],
            "pedido": record.get("texto") or spec.get("prompt_original") or "",
            "criterios": record.get("criterios") or [], "tentativa": int(record.get("tentativa") or 1),
            "mudanca": ((result.get("conferencia") or {}).get("mudanca_visual")),
        }
        get_conn().run(
            "INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, gen_type, created_at, updated_at) "
            "VALUES (?, ?, ?, 'interpret', 'queued', ?, 'evaluate', ?, ?)",
            (new_id(), job["user_id"], job["conversation_id"], dumps(eval_payload), now(), now()),
        )
    except Exception:  # noqa: BLE001 — a conferência nunca impede a entrega
        current_app.logger.exception("não foi possível agendar a conferência do job %s", job.get("id"))


def handle_evaluation(job, payload, verdict):
    """Veredito da conferência visual: confirma o que mudou, refaz uma vez com mais liberdade ou explica a limitação."""
    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (payload["conversation_id"],))
    msg_id = payload["assistant_message_id"]
    if not isinstance(verdict, dict):
        set_message(msg_id, conv["id"], "Pronto! Desta vez não consegui conferir a imagem automaticamente — me diga se "
                                        "ficou como você queria.")
        return
    g.user_id = conv["user_id"]
    lst = lambda v: [bf._clean_text(x, 140) for x in (v or [])[:4] if isinstance(x, str) and x.strip()]  # noqa: E731
    mudou, faltou = lst(verdict.get("mudou")), lst(verdict.get("faltou"))
    # "Nenhum", "nada", "-" significam que nada faltou; não podem reprovar a arte.
    faltou = [x for x in faltou if not re.fullmatch(r"\W*(nenhum[a]?|nada|n/?a|-+|sem pend[eê]ncias?)\W*", x, re.I)]
    resumo = bf._clean_text(verdict.get("resumo"), 300)
    try:
        nota = float(verdict.get("nota"))
    except (TypeError, ValueError):
        nota = None
    ok = verdict.get("atendeu") is True and (nota is None or nota >= 6) and not faltou
    render = get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (payload.get("render_job_id"),))
    spec_used = ((loads(render["payload"], {}) or {}).get("spec") or {}) if render else {}
    record = spec_used.get("pedido_ajuste") or {}
    if ok:
        text = "Conferi o resultado: " + (resumo or "; ".join(mudou) or "ficou como você pediu") + "."
        set_message(msg_id, conv["id"], text.replace("..", "."))
        return
    plano = creative.escalated_plan(spec_used, faltou) if payload.get("tentativa", 1) < 2 else None
    if plano and plano["estrategia"] != "so_texto":
        base = {k: v for k, v in spec_used.items() if k not in ("recompor", "pedido_ajuste")}
        if (spec_used.get("recompor") or {}).get("prompt_completo"):
            base["prompt_visual"] = spec_used["recompor"]["prompt_completo"]
        acao, _, spec2 = creative.plan_outcome(plano, {"prompt_visual": base.get("prompt_visual"), "estilo": base.get("style")},
                                               record.get("texto") or "", {}, base, record.get("arte_anterior"),
                                               conversation_texts(conv["id"]), attempt=2)
        if acao == "gerar":
            job2, problem = enqueue_free_image(conv, conv["user_id"], spec2)
            if job2:
                set_message(msg_id, conv["id"],
                            "Conferi e ainda não ficou como você pediu" + (f" (faltou: {'; '.join(faltou)})" if faltou else "")
                            + f". Já estou refazendo: vou {creative.STRATEGY_LABEL[plano['estrategia']]}.", job2)
                return
            if problem:
                faltou.append(problem)
    text = ("Conferi o resultado e preciso ser sincero: " + (resumo or "não ficou como você pediu") + "."
            + (f" Faltou: {'; '.join(faltou)}." if faltou else "")
            + " Quer que eu tente de outro jeito? Pode descrever o que imagina, ou peça para eu criar do zero.")
    set_message(msg_id, conv["id"], text.replace("..", "."))


def confirm_adjustment(job, report, art_bytes):
    """Depois de gerar: confere o pedido de ajuste (textos entregues e mudança real do fundo) e atualiza a resposta.

    Devolve o resumo da conferência (gravado no resultado do job)."""
    spec = (loads(job["payload"], {}) or {}).get("spec") or {}
    record = spec.get("pedido_ajuste")
    if not record:
        return None
    check = {"textos_ok": None, "mudanca_visual": None, "cena_ok": None}
    drawn = next((c for c in (report or {}).get("verificacoes") or [] if c.get("nome") == "textos_completos_e_identicos"), None)
    expected = [e["texto"] for e in record.get("esperado") or [] if e.get("texto")]
    if drawn is not None:
        check["textos_ok"] = bool(drawn.get("ok")) and all(t in (drawn.get("desenhado") or []) for t in expected)
    if record.get("cena") and record.get("arte_anterior"):
        old = get_conn().one("SELECT * FROM sg_assets WHERE id = ? AND user_id = ?", (record["arte_anterior"], job["user_id"]))
        if old:
            try:
                score = edits.visual_change(asset_bytes(old), art_bytes)
                check["mudanca_visual"] = score
                check["cena_ok"] = score >= edits.MIN_VISUAL_CHANGE.get(record["cena"], 0.05)
            except Exception:  # noqa: BLE001 — a conferência nunca impede a entrega
                current_app.logger.exception("conferência visual falhou no job %s", job["id"])
    notes = []
    if check["cena_ok"] is False:
        notes.append("O fundo mudou pouco em relação à versão anterior. Se quiser algo bem diferente, "
                     "diga “faça uma proposta totalmente nova”.")
    elif check["cena_ok"]:
        notes.append("Conferido: o fundo mudou.")
    if check["textos_ok"] is False:
        notes.append("Atenção: o texto entregue não ficou como o pedido; me avise que eu corrijo na hora.")
    elif check["textos_ok"] and expected:
        notes.append("Conferido: os textos estão como você pediu.")
    if notes and not interpreter_available():
        msg = get_conn().one("SELECT id, content FROM sg_messages WHERE job_id = ? AND role = 'assistant' "
                             "ORDER BY seq DESC LIMIT 1", (job["id"],))
        if msg:
            get_conn().run("UPDATE sg_messages SET content = ? WHERE id = ?", (msg["content"] + " " + " ".join(notes), msg["id"]))
    return check


def surface_note(job, report):
    """Se a superfície pedida não foi encontrada na cena, avisa o cliente de forma natural."""
    surface = (report or {}).get("superficie") or {}
    if surface.get("modo") != "destaque":
        return
    msg = get_conn().one("SELECT id, content FROM sg_messages WHERE job_id = ? AND role = 'assistant' ORDER BY seq DESC LIMIT 1",
                         (job["id"],))
    if msg and "destaque" not in (msg["content"] or ""):
        note = (" Não encontrei na cena um espaço liso e bem visível para o texto, então coloquei o texto em destaque "
                "sobre a arte. Se quiser, peça “gere outra versão” para tentar de novo.")
        get_conn().run("UPDATE sg_messages SET content = ? WHERE id = ?", (msg["content"] + note, msg["id"]))


def keep_known_products(proposed, known):
    """Junta os produtos do modelo com os já conhecidos (briefing anterior + leitura determinística da mensagem)."""
    proposed = dict(proposed)
    products = [dict(p) for p in proposed.get("produtos") or [] if isinstance(p, dict)]
    names = {bf.strip_accents(str(p.get("nome", "")).lower()) for p in products}
    for p in known.get("produtos") or []:
        if len(p["nome"].split()) > 4:  # frase inteira lida como "produto": descarta (não é um nome de produto)
            continue
        key = bf.strip_accents(p["nome"].lower())
        if not any(key in n or n in key for n in names if n):
            products.append(dict(p))
            names.add(key)
    proposed["produtos"] = products
    for field in ("loja", "formato", "validade", "titulo"):
        if not proposed.get(field) and known.get(field):
            proposed[field] = known[field]
    for field in ("layout", "confirmar", "gerado"):
        proposed.setdefault(field, known.get(field))
    return proposed


OPTIONAL_FIELD = re.compile(r"\b(nome da loja|loja|validade|formato|cen[aá]rio|t[ií]tulo)\b", re.IGNORECASE)


def drop_optional_questions(text):
    """Tira do texto do modelo as frases que pedem campos opcionais (loja, validade, formato…)."""
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    kept = [s for s in sentences if not (OPTIONAL_FIELD.search(s) and re.search(r"preciso|qual|informe|me diga|\?", s,
                                                                               re.IGNORECASE))]
    return " ".join(kept).strip()


def normalized_ready_except_photos(briefing):
    return bool(briefing.get("produtos")) and all(p.get("preco") for p in briefing["produtos"])


@bp.post("/worker/jobs/<job_id>/extracted")
@worker_required
def worker_extracted(job_id):
    """Redesign: produtos, preços e recortes das fotos originais lidos da arte do cliente -> briefing do encarte."""
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415

    job = worker_job(job_id)
    data = request.get_json(silent=True) or {}
    dados = data.get("dados") if isinstance(data.get("dados"), dict) else {}
    fotos = data.get("fotos") if isinstance(data.get("fotos"), list) else []
    produtos, conferir, pending_confirm = [], [], []
    for i, raw in enumerate((dados.get("produtos") or [])[:bf.MAX_PRODUCTS]):
        if not isinstance(raw, dict):
            continue
        asset_id = None
        b64 = fotos[i] if i < len(fotos) else None
        if b64:
            try:
                image, mime, width, height = sanitize_image(base64.b64decode(b64), 8 * 1024 * 1024, 40_000_000, 4096)
                asset_id = store_asset(job["user_id"], "product", image, mime, width, height)  # foto do próprio cliente
            except (ImageRejected, binascii.Error, ValueError):
                asset_id = None
        nome = bf._clean_text(raw.get("nome"), 80) or f"Produto {i + 1}"
        flags = [x for x in (raw.get("conferir") or []) if x in ("preco", "preco_de", "nome")]
        # Preço com leitura incerta NÃO é preenchido: o cliente confirma o valor (nunca publicar preço errado).
        preco = "" if "preco" in flags else (raw.get("preco") or "")
        preco_de = "" if "preco_de" in flags else (raw.get("preco_de") or "")
        produtos.append({"nome": nome, "preco": preco, "preco_de": preco_de, "unidade": "", "ean": "", "asset_id": asset_id})
        for f in flags:
            read = raw.get(f) or ""
            if f in ("preco", "preco_de"):
                pending_confirm.append({"produto": nome, "campo": f, "lido": f"R$ {read}" if read else ""})
            label = {"preco": "preço", "preco_de": "preço antigo (riscado)", "nome": "nome"}[f]
            conferir.append(f"{nome}: {label}" + (f" — li “R$ {read}”, confirme o valor" if read and f != "nome" else ""))
    conv = get_conn().one("SELECT * FROM sg_conversations WHERE id = ?", (job["conversation_id"],))
    previous = loads(conv["briefing"]) or bf.normalize_briefing({})
    briefing = {**previous, "confirmar": pending_confirm,
                "titulo": bf._clean_text(dados.get("titulo"), 60) or previous.get("titulo") or "Ofertas",
                "loja": bf._clean_text(dados.get("loja"), 60) or previous.get("loja", ""), "produtos": produtos,
                "referencias": list(dict.fromkeys((previous.get("referencias") or []) + (loads(job["payload"], {}) or {})
                                                  .get("asset_ids", [])))}
    owned = owned_asset_ids(job["user_id"], briefing_asset_ids(briefing))
    # Preços lidos da arte do próprio cliente (não inventados); os incertos ficam marcados para conferir.
    normalized = bf.normalize_briefing(briefing, [], owned, trust_prices=True)
    save_briefing(conv["id"], normalized)
    get_conn().run("UPDATE sg_conversations SET mode = 'ad' WHERE id = ?", (conv["id"],))
    get_conn().run("UPDATE sg_jobs SET status = 'done', progress = 100, status_message = 'Pronto', result = ?, "
                   "finished_at = ?, updated_at = ? WHERE id = ?",
                   (dumps({"extracao": data.get("relatorio") or {}, "produtos": len(produtos)}), now(), now(), job["id"]))
    lines = [f"• {p['nome']} — {p['preco'] or 'preço não lido'}" + (f" (de {p['preco_de']})" if p["preco_de"] else "")
             + ("" if p["asset_id"] else " — sem foto") for p in normalized["produtos"]]
    text = ("Li a sua arte. Produtos e preços encontrados:\n" + "\n".join(lines) if lines else
            "Não consegui ler os produtos da sua arte com segurança. Me passe os produtos e preços (ex.: “Leite 4,99”).")
    if conferir:
        text += ("\n\nNão preenchi os valores com leitura incerta (para não publicar preço errado): "
                 + "; ".join(conferir) + ". É só me responder aqui com o valor certo (ex.: “"
                 + (pending_confirm[0]["produto"].split()[0] if pending_confirm else "Arroz") + " 31,90”).")
    if lines:
        text += ("\n\nAs fotos são as originais da sua arte. Corrija o que precisar no painel e clique em “Gerar encarte” "
                 "para a versão com layout moderno.")
    msg = get_conn().one("SELECT id, content FROM sg_messages WHERE job_id = ? AND role = 'assistant' ORDER BY seq DESC LIMIT 1",
                         (job["id"],))
    if msg:
        get_conn().run("UPDATE sg_messages SET content = ? WHERE id = ?", (msg["content"] + "\n\n" + text, msg["id"]))
    return jsonify({"ok": True})


@bp.post("/worker/jobs/<job_id>/candidate")
@worker_required
def worker_candidate(job_id):
    """Uma foto encontrada na internet (busca web): conferida e guardada como opção do cliente."""
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415

    from .products import looks_like_barcode  # noqa: PLC0415

    job = worker_job(job_id)
    if (loads(job["payload"], {}) or {}).get("mode") != "websearch":
        return error("Tarefa inválida.")
    data = request.get_json(silent=True) or {}
    try:
        image, mime, width, height = sanitize_image(base64.b64decode(data.get("dados") or ""), cfg().max_upload_bytes * 2,
                                                    cfg().max_image_pixels, cfg().max_image_side)
    except (ImageRejected, binascii.Error, ValueError) as exc:
        return error(str(exc) or "imagem inválida")
    if looks_like_barcode(image) or min(width, height) < 120:
        return error("imagem recusada")
    page = bf._clean_text(data.get("pagina"), 400)
    asset_id = store_asset(job["user_id"], "candidate", image, mime, width, height, bf._clean_text(data.get("imagem"), 500),
                           "não informada (imagem de site)", bf._clean_text(data.get("fonte"), 80) + (f" — {page}" if page else ""))
    return jsonify({"ok": True, "asset_id": asset_id})


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
    if job.get("gen_type") == "reference_edit":
        msg = get_conn().one("SELECT id, content FROM sg_messages WHERE job_id = ? AND role = 'assistant' "
                             "ORDER BY seq DESC LIMIT 1", (job["id"],))
        if msg:
            note = (" Não consegui fazer essa troca com segurança na sua imagem (não encontrei o valor com clareza). "
                    "Pode me dizer qual texto da imagem devo trocar, exatamente como está escrito?")
            get_conn().run("UPDATE sg_messages SET content = ? WHERE id = ?", (msg["content"] + note, msg["id"]))
    if job["kind"] == "interpret":
        # A conversa não fica travada: cai para a interpretação por regras.
        payload = loads(job["payload"], {})
        g.user_id = job["user_id"]
        if side_job_failed(payload):
            return jsonify({"ok": True})
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
