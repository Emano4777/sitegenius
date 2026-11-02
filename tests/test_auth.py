"""Contas do Studio criadas pelo administrador: login, sessões, senha, limites e painel de admin."""
import re

import pytest

from studio import accounts
from studio.db import get_conn

from .conftest import make_app

PASSWORD = "senha-forte-123"


class Browser:
    """Cliente anônimo que lê o CSRF da página de login, como o navegador."""

    def __init__(self, app):
        self.app = app
        self.client = app.test_client()
        page = self.client.get("/studio/entrar")
        self.csrf = re.search(rb'name="sg-csrf" content="([^"]+)"', page.data).group(1).decode()

    def post(self, path, body, csrf=None):
        return self.client.post(path, json=body, headers={"X-SG-CSRF": csrf if csrf is not None else self.csrf})

    def patch(self, path, body):
        return self.client.patch(path, json=body, headers={"X-SG-CSRF": self.csrf})

    def refresh_csrf(self):
        with self.client.session_transaction() as sess:
            self.csrf = sess.get("sg_csrf") or self.csrf

    def login(self, email, password=PASSWORD):
        resp = self.post("/studio/auth/login", {"email": email, "senha": password})
        if resp.status_code == 200:
            self.csrf = resp.get_json()["csrf"]
        return resp

    def me(self):
        return self.client.get("/studio/api/me")


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def make_user(app, email="ana@exemplo.com", password=PASSWORD, **kw):
    kw.setdefault("must_change", False)
    with app.app_context():
        return accounts.create_user(get_conn(), "Ana", email, password, **kw)["id"]


def admin_browser(app):
    make_user(app, "admin@exemplo.com", is_admin=True)
    b = Browser(app)
    assert b.login("admin@exemplo.com").status_code == 200
    return b


# ------------------------------------------------------------ login e sessões


def test_public_signup_is_disabled(app):
    resp = Browser(app).post("/studio/auth/cadastro", {"nome": "X", "email": "x@exemplo.com", "senha": PASSWORD})
    assert resp.status_code == 403 and "administrador" in resp.get_json()["erro"]
    assert b"Criar conta" not in app.test_client().get("/studio/entrar").data


def test_login_logout_flow(app):
    make_user(app)
    b = Browser(app)
    assert b.login("ANA@exemplo.com ").status_code == 200
    me = b.me().get_json()
    assert me["nome"] == "Ana" and me["admin"] is False and me["uso"]["hoje"] == 0
    assert b.client.get("/studio/").status_code == 200
    assert b.post("/studio/auth/logout", {}).status_code == 200
    assert b.me().status_code == 401


def test_password_is_hashed(app):
    make_user(app)
    with app.app_context():
        row = get_conn().one("SELECT password_hash FROM sg_users WHERE email = ?", ("ana@exemplo.com",))
    assert PASSWORD not in row["password_hash"] and row["password_hash"].startswith(("scrypt:", "pbkdf2:"))


def test_wrong_password_and_unknown_email_get_same_answer(app):
    make_user(app)
    wrong = Browser(app).post("/studio/auth/login", {"email": "ana@exemplo.com", "senha": "errada-errada"})
    unknown = Browser(app).post("/studio/auth/login", {"email": "ninguem@exemplo.com", "senha": "errada-errada"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json()


def test_auth_endpoints_require_csrf(app):
    make_user(app)
    b = Browser(app)
    assert b.post("/studio/auth/login", {"email": "ana@exemplo.com", "senha": PASSWORD}, csrf="outro").status_code == 403


def test_login_is_rate_limited(app):
    make_user(app)
    b = Browser(app)
    codes = [b.post("/studio/auth/login", {"email": "ana@exemplo.com", "senha": "errada-errada"}).status_code
             for _ in range(11)]
    assert codes[-1] == 429
    assert b.post("/studio/auth/login", {"email": "ana@exemplo.com", "senha": PASSWORD}).status_code == 429


def test_password_reset_flow_invalidates_old_sessions(app, monkeypatch):
    sent = {}
    monkeypatch.setattr(accounts, "send_reset_email", lambda to, name, link: sent.update(to=to, link=link) or True)
    make_user(app)
    logged = Browser(app)
    logged.login("ana@exemplo.com")
    b = Browser(app)
    resp = b.post("/studio/auth/esqueci-senha", {"email": "ana@exemplo.com"})
    unknown = Browser(app).post("/studio/auth/esqueci-senha", {"email": "ninguem@exemplo.com"})
    assert resp.get_json() == unknown.get_json()
    token = sent["link"].split("#t=")[1]
    assert b.post("/studio/auth/redefinir-senha", {"token": token, "senha": "nova-senha-456"}).status_code == 200
    assert logged.me().status_code == 401
    assert Browser(app).post("/studio/auth/redefinir-senha", {"token": token, "senha": "outra-senha-789"}).status_code == 400


def test_expired_reset_token_is_rejected(app, monkeypatch):
    sent = {}
    monkeypatch.setattr(accounts, "send_reset_email", lambda to, name, link: sent.update(link=link) or True)
    make_user(app)
    Browser(app).post("/studio/auth/esqueci-senha", {"email": "ana@exemplo.com"})
    with app.app_context():
        get_conn().run("UPDATE sg_password_resets SET expires_at = 1")
    token = sent["link"].split("#t=")[1]
    assert Browser(app).post("/studio/auth/redefinir-senha", {"token": token, "senha": "nova-senha-456"}).status_code == 400


# ------------------------------------------------------------ senha provisória e troca obrigatória


def test_temporary_password_must_be_changed_first(app):
    make_user(app, must_change=True)
    b = Browser(app)
    b.login("ana@exemplo.com")
    assert b.me().get_json()["trocar_senha"] is True
    assert b.client.get("/studio/admin").status_code == 302  # páginas levam ao Studio (tela de criar senha)
    blocked = b.client.post("/studio/api/conversations", json={}, headers={"X-SG-CSRF": b.csrf})
    assert blocked.status_code == 403 and "senha" in blocked.get_json()["erro"]
    assert b.post("/studio/auth/trocar-senha", {"atual": "errada", "nova": "nova-senha-456"}).status_code == 400
    resp = b.post("/studio/auth/trocar-senha", {"atual": PASSWORD, "nova": "nova-senha-456"})
    assert resp.status_code == 200
    b.csrf = resp.get_json()["csrf"]
    assert b.me().get_json()["trocar_senha"] is False
    assert b.client.post("/studio/api/conversations", json={}, headers={"X-SG-CSRF": b.csrf}).status_code == 201


# ------------------------------------------------------------ painel de administração


def test_admin_area_is_restricted(app):
    make_user(app)
    b = Browser(app)
    b.login("ana@exemplo.com")
    assert b.client.get("/studio/admin").status_code == 302
    assert b.client.get("/studio/api/admin/users").status_code == 403
    assert b.post("/studio/api/admin/users", {"nome": "X", "email": "x@exemplo.com"}).status_code == 403
    assert app.test_client().get("/studio/api/admin/stats").status_code == 401


def test_admin_creates_user_with_limits_and_temp_password(app):
    admin = admin_browser(app)
    assert admin.client.get("/studio/admin").status_code == 200
    resp = admin.post("/studio/api/admin/users", {"nome": "Bruno", "email": "bruno@exemplo.com",
                                                   "limite_diario": 3, "limite_mensal": 40})
    assert resp.status_code == 201
    temp = resp.get_json()["senha_provisoria"]
    users = admin.client.get("/studio/api/admin/users").get_json()["usuarios"]
    bruno = next(u for u in users if u["email"] == "bruno@exemplo.com")
    assert bruno["limite_diario"] == 3 and bruno["limite_mensal"] == 40 and bruno["trocar_senha"] is True
    with app.app_context():
        stored = get_conn().one("SELECT password_hash FROM sg_users WHERE email = 'bruno@exemplo.com'")["password_hash"]
    assert temp not in stored
    user = Browser(app)
    assert user.login("bruno@exemplo.com", temp).status_code == 200
    assert user.me().get_json()["trocar_senha"] is True
    assert admin.post("/studio/api/admin/users", {"nome": "B2", "email": "BRUNO@exemplo.com"}).status_code == 400


def test_admin_limits_are_enforced(app):
    admin = admin_browser(app)
    uid = make_user(app)
    assert admin.patch(f"/studio/api/admin/users/{uid}", {"limite_diario": 1}).status_code == 200
    b = Browser(app)
    b.login("ana@exemplo.com")
    h = {"X-SG-CSRF": b.csrf}
    conv = b.client.post("/studio/api/conversations", json={}, headers=h).get_json()["id"]
    b.client.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "um farol"}, headers=h)
    b.client.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "uma montanha"}, headers=h)
    data = b.client.get(f"/studio/api/conversations/{conv}").get_json()
    assert len(data["jobs"]) == 1 and "limite de 1 imagem por dia" in data["mensagens"][-1]["texto"]
    assert b.me().get_json()["uso"] == {"hoje": 1, "mes": 1, "total": 1, "limite_diario": 1, "limite_mensal": None}
    assert admin.patch(f"/studio/api/admin/users/{uid}", {"limite_diario": 50, "limite_mensal": 1}).status_code == 200
    b.client.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "um rio"}, headers=h)
    data = b.client.get(f"/studio/api/conversations/{conv}").get_json()
    assert "limite de 1 imagem neste mês" in data["mensagens"][-1]["texto"]


def test_admin_deactivates_and_resets_password(app):
    admin = admin_browser(app)
    uid = make_user(app)
    user = Browser(app)
    user.login("ana@exemplo.com")
    assert admin.patch(f"/studio/api/admin/users/{uid}", {"ativo": False}).status_code == 200
    assert user.me().status_code == 401  # sessão derrubada
    assert Browser(app).login("ana@exemplo.com").status_code == 401
    assert admin.patch(f"/studio/api/admin/users/{uid}", {"ativo": True}).status_code == 200
    temp = admin.post(f"/studio/api/admin/users/{uid}/reset-password", {}).get_json()["senha_provisoria"]
    assert Browser(app).login("ana@exemplo.com").status_code == 401  # senha antiga não vale mais
    assert Browser(app).login("ana@exemplo.com", temp).status_code == 200


def test_admin_cannot_lock_himself_out(app):
    admin = admin_browser(app)
    me = admin.me().get_json()["user_id"]
    assert admin.patch(f"/studio/api/admin/users/{me}", {"ativo": False}).status_code == 400
    assert admin.patch(f"/studio/api/admin/users/{me}", {"admin": False}).status_code == 400


def test_admin_sees_technical_activity(app):
    admin = admin_browser(app)
    assert admin.client.get("/studio/api/admin/jobs").get_json()["jobs"] == []
    make_user(app)
    b = Browser(app)
    b.login("ana@exemplo.com")
    assert b.client.get("/studio/api/admin/jobs").status_code == 403


def test_admin_stats_count_generated_images(app):
    admin = admin_browser(app)
    stats = admin.client.get("/studio/api/admin/stats").get_json()
    assert stats["usuarios_ativos"] == 1 and stats["imagens_total"] == 0 and len(stats["ultimos_14_dias"]) == 14


def test_users_only_see_their_own_conversations(app):
    make_user(app, "a@exemplo.com")
    make_user(app, "b@exemplo.com")
    a, b = Browser(app), Browser(app)
    a.login("a@exemplo.com")
    b.login("b@exemplo.com")
    conv = a.client.post("/studio/api/conversations", json={}, headers={"X-SG-CSRF": a.csrf}).get_json()["id"]
    assert b.client.get(f"/studio/api/conversations/{conv}").status_code == 404
    assert a.client.get(f"/studio/api/conversations/{conv}").status_code == 200


def test_day_and_month_boundaries_use_brasilia_time():
    from studio.routes import day_start, month_start

    ts = 1793581200  # 2026-11-02 01:00 UTC = 2026-11-01 22:00 em Brasília
    assert day_start(ts) == 1793502000      # 2026-11-01 00:00 BRT (03:00 UTC)
    assert month_start(ts) == 1793502000    # 1º de novembro 00:00 BRT
