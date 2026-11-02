"""Integração com o app.py real (sem acessar banco/serviços externos)."""
import logging
import os

import pytest


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    os.environ.setdefault("OPENAI_API_KEY", "dummy-local-test")
    os.environ["SG_STUDIO_DATABASE_URL"] = "sqlite:///" + str(tmp_path_factory.mktemp("db") / "s.db")
    os.environ.pop("VERCEL", None)
    logging.disable(logging.CRITICAL)
    import app as site_module

    logging.disable(logging.NOTSET)

    def no_production_db():
        raise AssertionError("teste tentou acessar o banco de produção")

    site_module.get_db_connection = no_production_db
    return site_module.app


def test_existing_routes_still_resolve(site):
    adapter = site.url_map.bind("sitegenius.com.br")
    assert adapter.match("/minhaloja")[0] == "index_loja"
    assert adapter.match("/minhaloja/produtos")[0] == "produtos"
    assert adapter.match("/login")[0] == "login"
    assert adapter.match("/studio/")[0] == "studio.studio_page"
    assert adapter.match("/studio/api/conversations")[0] == "studio.api_conversations"


def test_studio_without_slash_redirects(site):
    resp = site.test_client().get("/studio")
    assert resp.status_code == 308
    assert resp.headers["Location"].endswith("/studio/")


def test_studio_requires_login(site):
    client = site.test_client()
    resp = client.get("/studio/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/studio/entrar")
    assert client.get("/studio/api/me").status_code == 401
    assert client.get("/studio/entrar").status_code == 200


def test_cors_excludes_studio_but_keeps_site(site):
    client = site.test_client()
    origin = {"Origin": "https://evil.example"}
    studio = client.get("/studio/api/me", headers=origin)
    assert "Access-Control-Allow-Origin" not in studio.headers
    site_resp = client.get("/ads.txt", headers=origin)
    assert "Access-Control-Allow-Origin" in site_resp.headers


def test_old_site_login_does_not_reach_studio(site):
    client = site.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 42  # sessão do login antigo do SiteGenius
    assert client.get("/studio/").status_code == 302
    assert client.get("/studio/api/me").status_code == 401


def test_vercel_requires_flask_secret_key():
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if k != "FLASK_SECRET_KEY"}
    env.update(VERCEL_ENV="preview", OPENAI_API_KEY="dummy-local-test", PYTHONIOENCODING="utf-8")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", "import app"], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0
    assert "FLASK_SECRET_KEY" in proc.stderr


def test_no_hardcoded_credentials_in_python_sources():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"APP_USR-|postgres(ql)?://[^\"'\s]*:[^\"'\s@]+@|api_secret\s*=\s*['\"]")
    found = [str(p.relative_to(root)) for p in root.glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    found += [str(p.relative_to(root)) for d in ("routes", "studio", "workers")
              for p in (root / d).glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert found == []


def test_moderation_is_scoped_to_owner(site, monkeypatch):
    import app as site_module

    executed = []

    class Cur:
        rowcount = 0

        def execute(self, sql, params):
            executed.append((sql, params))

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

        def commit(self):
            pass

    monkeypatch.setattr(site_module, "get_db_connection", lambda: Conn())
    client = site.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 7
    for path in ("/admin/eventos/moderar", "/admin/eventos/excluir",
                 "/admin/classificados/moderar", "/admin/classificados/excluir"):
        resp = client.post(path, json={"id": 99, "aprovar": True})
        assert resp.status_code == 403  # nenhuma linha do usuário 7
    assert len(executed) == 4
    for sql, params in executed:
        assert "user_templates WHERE user_id = %s" in sql and params[-1] == 7
