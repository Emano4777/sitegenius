"""Contas do Studio: cadastro, login e recuperação de senha no banco do próprio Studio.

Não usa nem consulta o login antigo do SiteGenius.
"""
import hashlib
import json
import logging
import os
import secrets
import urllib.error
import urllib.request

from email_validator import EmailNotValidError, validate_email
from werkzeug.security import check_password_hash, generate_password_hash

from .db import now

log = logging.getLogger(__name__)

PASSWORD_MIN, PASSWORD_MAX = 10, 128
RESET_TTL_S = 30 * 60
SESSION_MAX_AGE_S = 14 * 24 * 3600
# Hash fixo para gastar o mesmo tempo quando o e-mail não existe (evita descobrir contas pelo tempo).
_DUMMY_HASH = generate_password_hash("sitegenius-studio-dummy-password")


class AccountError(ValueError):
    pass


def normalize_email(raw):
    try:
        return validate_email((raw or "").strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise AccountError("Informe um e-mail válido.") from None


def check_password_policy(password, email=""):
    if not isinstance(password, str) or not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise AccountError(f"A senha precisa ter entre {PASSWORD_MIN} e {PASSWORD_MAX} caracteres.")
    if email and password.lower() == email.lower():
        raise AccountError("A senha não pode ser igual ao e-mail.")


USER_FIELDS = ("id, email, name, session_version, active, is_admin, daily_limit, monthly_limit, "
               "must_change_password, created_at, last_login_at, notes")


def generate_temp_password():
    """Senha provisória (mostrada uma única vez ao admin; o usuário troca no primeiro acesso)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


def clean_limit(value):
    """Limite de imagens: None = padrão do sistema (diário) ou sem limite (mensal)."""
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise AccountError("Limite inválido.") from None
    if not 0 <= value <= 100000:
        raise AccountError("Limite fora da faixa (0 a 100000).")
    return value


def create_user(conn, name, email, password, *, is_admin=False, daily_limit=None, monthly_limit=None,
                must_change=True, created_by=None, notes=""):
    name = " ".join((name or "").split())[:120]
    if len(name) < 2:
        raise AccountError("Informe o nome.")
    email = normalize_email(email)
    check_password_policy(password, email)
    if conn.one("SELECT id FROM sg_users WHERE email = ?", (email,)):
        raise AccountError("Já existe uma conta com esse e-mail.")
    ts = now()
    row = conn.one(
        "INSERT INTO sg_users (email, name, password_hash, is_admin, daily_limit, monthly_limit, must_change_password, "
        "created_by, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (email, name, generate_password_hash(password), 1 if is_admin else 0, clean_limit(daily_limit),
         clean_limit(monthly_limit), 1 if must_change else 0, created_by, " ".join(str(notes or "").split())[:300], ts, ts),
    )
    return get_user(conn, row["id"])


def get_user(conn, user_id):
    return conn.one(f"SELECT {USER_FIELDS} FROM sg_users WHERE id = ? AND active = 1", (user_id,))


def change_password(conn, user_id, current, new):
    row = conn.one("SELECT email, password_hash FROM sg_users WHERE id = ? AND active = 1", (user_id,))
    if not row or not check_password_hash(row["password_hash"], current or ""):
        raise AccountError("Senha atual incorreta.")
    if current == new:
        raise AccountError("A nova senha precisa ser diferente da atual.")
    check_password_policy(new, row["email"])
    conn.run(
        "UPDATE sg_users SET password_hash = ?, must_change_password = 0, session_version = session_version + 1, "
        "updated_at = ? WHERE id = ?",
        (generate_password_hash(new), now(), user_id),
    )
    return get_user(conn, user_id)


def admin_reset_password(conn, user_id):
    """Gera senha provisória, derruba as sessões abertas e exige troca no próximo acesso."""
    temp = generate_temp_password()
    changed = conn.run(
        "UPDATE sg_users SET password_hash = ?, must_change_password = 1, session_version = session_version + 1, "
        "updated_at = ? WHERE id = ?",
        (generate_password_hash(temp), now(), user_id),
    )
    if not changed:
        raise AccountError("Usuário não encontrado.")
    conn.run("DELETE FROM sg_password_resets WHERE user_id = ?", (user_id,))
    return temp


def authenticate(conn, email, password):
    try:
        email = normalize_email(email)
    except AccountError:
        email = ""
    row = conn.one("SELECT id, password_hash FROM sg_users WHERE email = ? AND active = 1", (email,)) if email else None
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password or "")
    if not (row and ok):
        return None
    conn.run("UPDATE sg_users SET last_login_at = ? WHERE id = ?", (now(), row["id"]))
    return get_user(conn, row["id"])


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token(conn, email):
    """Devolve (usuário, token) ou (None, None). Quem chama nunca revela se o e-mail existe."""
    try:
        email = normalize_email(email)
    except AccountError:
        return None, None
    user = conn.one("SELECT id, email, name FROM sg_users WHERE email = ? AND active = 1", (email,))
    if not user:
        return None, None
    conn.run("DELETE FROM sg_password_resets WHERE user_id = ?", (user["id"],))
    token = secrets.token_urlsafe(32)
    ts = now()
    conn.run(
        "INSERT INTO sg_password_resets (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user["id"], ts + RESET_TTL_S, ts),
    )
    return user, token


def reset_password(conn, token, password):
    row = conn.one(
        "SELECT r.user_id, u.email FROM sg_password_resets r JOIN sg_users u ON u.id = r.user_id "
        "WHERE r.token_hash = ? AND r.used_at IS NULL AND r.expires_at > ? AND u.active = 1",
        (_token_hash(token or ""), now()),
    )
    if not row:
        raise AccountError("Link inválido ou expirado. Peça um novo.")
    check_password_policy(password, row["email"])
    # Trocar a senha invalida todas as sessões abertas (session_version) e os outros links.
    conn.run(
        "UPDATE sg_users SET password_hash = ?, session_version = session_version + 1, updated_at = ? WHERE id = ?",
        (generate_password_hash(password), now(), row["user_id"]),
    )
    conn.run("DELETE FROM sg_password_resets WHERE user_id = ?", (row["user_id"],))
    return get_user(conn, row["user_id"])


def send_reset_email(to, name, link):
    """Envia pelo Resend (mesma integração do dns-ecommerce, credenciais só por ambiente)."""
    key, sender = os.getenv("SG_RESEND_API_KEY", ""), os.getenv("SG_MAIL_FROM", "")
    if not key or not sender:
        log.warning("e-mail de recuperação não enviado: SG_RESEND_API_KEY/SG_MAIL_FROM não configurados")
        return False
    html = (
        f"<p>Olá, {_escape(name)}.</p><p>Para criar uma nova senha no SiteGenius Studio, acesse o link abaixo "
        f"(válido por 30 minutos):</p><p><a href=\"{_escape(link)}\">Criar nova senha</a></p>"
        "<p>Se você não pediu, ignore este e-mail.</p>"
    )
    payload = json.dumps({"from": sender, "to": [to], "subject": "SiteGenius Studio: nova senha", "html": html})
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload.encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "SiteGenius-Studio"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        log.warning("falha ao enviar e-mail de recuperação: %s", type(exc).__name__)
        return False


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))
