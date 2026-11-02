import io
import os
import sys
from urllib.parse import urlsplit

import pytest
from flask import Flask
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import init_app  # noqa: E402
from studio.config import StudioConfig  # noqa: E402
from studio.db import get_conn, now  # noqa: E402
from studio.worker_client import WorkerClient  # noqa: E402

RENDER_SECRET = "r" * 40
INTERPRET_SECRET = "i" * 40


def make_app(tmp_path, secret_key="test-secret", **overrides):
    app = Flask(__name__, instance_path=str(tmp_path))
    app.secret_key = secret_key
    app.config["TESTING"] = True
    config = StudioConfig(
        database_url="sqlite:///" + str(tmp_path / "studio.db"),
        workers={
            "acer": {"kind": "render", "secret": RENDER_SECRET.encode()},
            "hostgator": {"kind": "interpret", "secret": INTERPRET_SECRET.encode()},
        },
        **overrides,
    )
    init_app(app, config)
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


class UserClient:
    """Cliente de teste logado, que envia o CSRF automaticamente."""

    def __init__(self, app, user_id):
        self.client = app.test_client()
        with app.app_context():
            conn = get_conn()
            if not conn.one("SELECT id FROM sg_users WHERE id = ?", (user_id,)):
                conn.run(
                    "INSERT INTO sg_users (id, email, name, password_hash, created_at, updated_at) VALUES (?, ?, ?, 'x', ?, ?)",
                    (user_id, f"user{user_id}@teste.com", f"Usuário {user_id}", now(), now()),
                )
        with self.client.session_transaction() as sess:
            sess.update(sg_uid=user_id, sg_sv=1, sg_auth_at=now(), sg_csrf=f"csrf-{user_id}")
        self.csrf = f"csrf-{user_id}"

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def _send(self, method, path, **kw):
        headers = kw.pop("headers", {})
        headers.setdefault("X-SG-CSRF", self.csrf)
        return getattr(self.client, method)(path, headers=headers, **kw)

    def post(self, path, **kw):
        return self._send("post", path, **kw)

    def put(self, path, **kw):
        return self._send("put", path, **kw)


@pytest.fixture
def user(app):
    return UserClient(app, 7)


@pytest.fixture
def other_user(app):
    return UserClient(app, 8)


class _FlaskResponse:
    def __init__(self, resp):
        self.status_code = resp.status_code
        self.content = resp.data
        self._resp = resp

    def json(self):
        return self._resp.get_json()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.content[:200]!r}")


class FlaskSession:
    """Faz o WorkerClient (requests) falar com o app de teste."""

    def __init__(self, app):
        self.client = app.test_client()

    def request(self, method, url, data=None, headers=None, timeout=None):
        path = urlsplit(url).path
        resp = self.client.open(path, method=method, data=data or b"", headers=headers or {})
        return _FlaskResponse(resp)


@pytest.fixture
def render_worker(app):
    return WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))


@pytest.fixture
def interpret_worker(app):
    return WorkerClient("http://localhost", "hostgator", INTERPRET_SECRET, session=FlaskSession(app))


def image_bytes(fmt="PNG", size=(200, 160), color=(200, 30, 30)):
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()
