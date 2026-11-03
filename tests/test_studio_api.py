import io
import time

import pytest
from PIL import Image

from studio import security
from studio.db import Conn
from studio.security import UnsafeUrl, check_url, sign
from tests.conftest import RENDER_SECRET, UserClient, image_bytes, make_app


def new_conversation(user):
    resp = user.post("/studio/api/conversations", json={})
    assert resp.status_code == 201
    return resp.get_json()["id"]


def upload(user, kind="product", data=None):
    resp = user.post(
        "/studio/api/uploads",
        data={"tipo": kind, "arquivo": (io.BytesIO(data or image_bytes()), "foto.png")},
        content_type="multipart/form-data",
    )
    return resp


# ------------------------------------------------------------ autenticação e CSRF


def test_requires_login(app):
    client = app.test_client()
    assert client.get("/studio/api/conversations").status_code == 401
    assert client.get("/studio/").status_code == 302


def test_page_renders_with_csp(user):
    resp = user.get("/studio/")
    assert resp.status_code == 200
    assert b'name="sg-csrf"' in resp.data
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_csrf_and_origin_are_enforced(user):
    resp = user.client.post("/studio/api/conversations", json={})
    assert resp.status_code == 403
    resp = user.post("/studio/api/conversations", json={}, headers={"X-SG-CSRF": user.csrf, "Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_public_secret_key_on_vercel_disables_studio(tmp_path):
    # A chave antiga está no repositório público: sessões seriam forjáveis.
    app = make_app(tmp_path, secret_key="minha-chave-segura", is_vercel=True)
    resp = UserClient(app, 1).get("/studio/api/me")
    assert resp.status_code == 503
    assert "manutenção" in resp.get_json()["erro"]  # o motivo técnico vai só para o log


# ------------------------------------------------------------ chat e briefing


def test_chat_rules_flow_builds_briefing(user):
    conv = new_conversation(user)
    resp = user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Feed. Arroz 5kg 24,90\nFeijão 1kg 7,49"})
    assert resp.status_code == 201
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert [m["role"] for m in data["mensagens"]] == ["assistant", "user", "assistant"]
    assert [p["preco"] for p in data["briefing"]["produtos"]] == ["R$ 24,90", "R$ 7,49"]
    assert data["pode_gerar"] is True  # foto é recomendada, preço é obrigatório
    assert "Arroz" in data["mensagens"][-1]["texto"]


def test_conversations_are_private(user, other_user):
    conv = new_conversation(user)
    assert other_user.get(f"/studio/api/conversations/{conv}").status_code == 404
    assert other_user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "oi"}).status_code == 404
    assert other_user.get("/studio/api/conversations").get_json()["conversas"] == []


def test_upload_validation(user):
    ok = upload(user, data=image_bytes("JPEG"))
    assert ok.status_code == 201
    bad = upload(user, data=b"<?php echo 1; ?>")
    assert bad.status_code == 400
    assert upload(user, kind="script").status_code == 400
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    assert upload(user, data=gif.getvalue()).status_code == 400


def test_upload_strips_metadata_and_is_private(user, other_user):
    img = Image.new("RGB", (50, 50), (10, 200, 10))
    exif = Image.Exif()
    exif[0x010E] = "segredo GPS"
    raw = io.BytesIO()
    img.save(raw, "JPEG", exif=exif)
    asset_id = upload(user, data=raw.getvalue()).get_json()["asset"]["id"]
    resp = user.get(f"/studio/api/assets/{asset_id}")
    assert resp.status_code == 200
    assert b"segredo GPS" not in resp.data
    assert other_user.get(f"/studio/api/assets/{asset_id}").status_code == 404


def test_briefing_rejects_foreign_assets(user, other_user):
    foreign = upload(other_user).get_json()["asset"]["id"]
    conv = new_conversation(user)
    resp = user.put(
        f"/studio/api/conversations/{conv}/briefing",
        json={"briefing": {"produtos": [{"nome": "Arroz", "preco": "10,00", "asset_id": foreign}]}},
    )
    assert resp.get_json()["briefing"]["produtos"][0]["asset_id"] is None


def test_attachment_of_other_user_is_refused(user, other_user):
    foreign = upload(other_user).get_json()["asset"]["id"]
    conv = new_conversation(user)
    resp = user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "veja", "anexos": [foreign]})
    assert resp.status_code == 403


def test_rate_limit_on_messages(user):
    conv = new_conversation(user)
    codes = [user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": f"oi {i}"}).status_code for i in range(32)]
    assert codes.count(429) >= 1


# ------------------------------------------------------------ geração com worker (Acer)


def prepared_conversation(user):
    conv = new_conversation(user)
    photo = upload(user, data=image_bytes(color=(250, 200, 0))).get_json()["asset"]["id"]
    logo = upload(user, kind="logo", data=image_bytes(size=(300, 120))).get_json()["asset"]["id"]
    user.put(
        f"/studio/api/conversations/{conv}/briefing",
        json={
            "briefing": {
                "formato": "quadrado",
                "titulo": "Ofertas da semana",
                "loja": "Mercado Teste",
                "validade": "01/11 a 05/11",
                "logo_asset_id": logo,
                "produtos": [{"nome": "Arroz 5kg", "preco": "24,90", "asset_id": photo}],
                # Fundo gerado pelo Acer (Q3): o único caso em que o painel enfileira o gerador de imagens.
                "layout": {"fundo_ia": True},
            }
        },
    )
    return conv, photo, logo


def test_render_job_full_cycle(user, other_user, render_worker):
    conv, photo, logo = prepared_conversation(user)
    resp = user.post(f"/studio/api/conversations/{conv}/jobs")
    assert resp.status_code == 201, resp.get_json()
    job_id = resp.get_json()["job"]["id"]

    job = render_worker.claim()
    assert job["id"] == job_id
    payload = job["payload"]
    assert payload["scene_size"] == [832, 832]
    assert "no text" in payload["scene_prompt"]
    assert set(payload["asset_ids"]) == {photo, logo}
    assert render_worker.claim() is None  # nada mais na fila

    assert render_worker.asset(photo)[:3] == b"\xff\xd8\xff"  # PNG sem alfa vira JPEG na sanitização
    # Asset fora do job não pode ser baixado pelo worker.
    foreign = upload(other_user).get_json()["asset"]["id"]
    with pytest.raises(RuntimeError):
        render_worker.asset(foreign)

    render_worker.progress(job_id, 40, "Gerando cenário")
    status = user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]
    assert status["status"] == "running" and status["progress"] == 40

    result = render_worker.complete_image(job_id, image_bytes("JPEG", (1080, 1080)))
    status = user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]
    assert status["status"] == "done"
    assert status["resultado_asset_id"] == result["asset_id"]
    download = user.get(f"/studio/api/assets/{result['asset_id']}?download=1")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]
    assert other_user.get(f"/studio/api/jobs/{job_id}").status_code == 404


def test_panel_button_composes_flyer_on_server(user, render_worker):
    """Botão "Gerar encarte" = mesmo fluxo do chat: composição imediata no servidor, sem depender da fila."""
    conv = new_conversation(user)
    photo = upload(user, data=image_bytes(color=(250, 200, 0))).get_json()["asset"]["id"]
    user.put(f"/studio/api/conversations/{conv}/briefing", json={"briefing": {
        "formato": "quadrado", "loja": "Mercado Teste", "produtos": [{"nome": "Arroz 5kg", "preco": "24,90", "asset_id": photo}]}})
    job = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()["job"]
    assert job["status"] == "done" and job["tipo"] == "flyer" and render_worker.claim() is None
    again = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()
    assert again["job"] is None and "já está pronto" in again["mensagem"]  # sem duplicar


def test_job_requires_prices(user):
    conv = new_conversation(user)
    user.put(f"/studio/api/conversations/{conv}/briefing", json={"briefing": {"produtos": [{"nome": "Arroz"}]}})
    resp = user.post(f"/studio/api/conversations/{conv}/jobs")
    assert resp.status_code == 400
    assert "Sem preço" in resp.get_json()["erro"]


def test_cancel_and_worker_cannot_finish_canceled(user, render_worker):
    conv, _, _ = prepared_conversation(user)
    job_id = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()["job"]["id"]
    render_worker.claim()
    assert user.post(f"/studio/api/jobs/{job_id}/cancel").status_code == 200
    with pytest.raises(RuntimeError):
        render_worker.complete_image(job_id, image_bytes("JPEG"))


def test_daily_quota(tmp_path):
    app = make_app(tmp_path, daily_job_quota=1)
    user = UserClient(app, 3)
    conv, _, _ = prepared_conversation(user)
    assert user.post(f"/studio/api/conversations/{conv}/jobs").status_code == 201
    assert user.post(f"/studio/api/conversations/{conv}/jobs").status_code == 429


def test_expired_lease_is_requeued(app, user, render_worker):
    conv, _, _ = prepared_conversation(user)
    job_id = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()["job"]["id"]
    render_worker.claim()
    conn = Conn(app.extensions["sg_studio"]["db"])
    conn.run("UPDATE sg_jobs SET lease_until = 1 WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    again = render_worker.claim()
    assert again["id"] == job_id


# ------------------------------------------------------------ assinatura dos workers


def signed_headers(path, body=b"{}", secret=RENDER_SECRET, ts=None, nonce="n" * 32, worker="acer"):
    ts = str(ts or int(time.time()))
    return {
        "X-SG-Worker": worker,
        "X-SG-Timestamp": ts,
        "X-SG-Nonce": nonce,
        "X-SG-Signature": sign(secret.encode(), ts, nonce, "POST", path, body),
        "Content-Type": "application/json",
    }


def test_worker_signature_checks(app):
    client = app.test_client()
    path = "/studio/worker/claim"
    assert client.post(path, data=b"{}").status_code == 401
    assert client.post(path, data=b"{}", headers=signed_headers(path, secret="x" * 40)).status_code == 401
    assert client.post(path, data=b"{}", headers=signed_headers(path, ts=int(time.time()) - 3600)).status_code == 401
    assert client.post(path, data=b'{"x":1}', headers=signed_headers(path)).status_code == 401  # corpo alterado
    good = signed_headers(path, nonce="a" * 32)
    assert client.post(path, data=b"{}", headers=good).status_code == 204
    assert client.post(path, data=b"{}", headers=good).status_code == 401  # replay


def test_render_worker_cannot_take_interpret_jobs(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 5)
    conv = new_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Arroz 10,00"})
    from tests.conftest import FlaskSession
    from studio.worker_client import WorkerClient

    render = WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))
    assert render.claim() is None


# ------------------------------------------------------------ interpretação pela IA (HostGator)


@pytest.fixture
def queue_app(tmp_path):
    return make_app(tmp_path, interpreter_mode="queue", interpret_timeout_s=60)


def test_interpret_queue_flow_drops_invented_prices(queue_app):
    from tests.conftest import INTERPRET_SECRET, FlaskSession
    from studio.worker_client import WorkerClient

    user = UserClient(queue_app, 9)
    worker = WorkerClient("http://localhost", "hostgator", INTERPRET_SECRET, session=FlaskSession(queue_app))
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    conv = new_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Encarte de natal: Panetone 19,90 e Chocotone", "anexos": [ref]})

    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert data["mensagens"][-1]["status"] == "pending"

    job = worker.claim()
    assert job["kind"] == "interpret"
    assert job["payload"]["image_asset_ids"] == [ref]
    assert worker.asset(ref)  # pode ver a referência para análise visual
    worker.complete_json(
        job["id"],
        {
            "resposta": "Montei o briefing de Natal.",
            "briefing": {
                "formato": "story",
                "tema": "natal",
                "cenario": "warm christmas table",
                "produtos": [{"nome": "Panetone", "preco": "19,90"}, {"nome": "Chocotone", "preco": "22,90"}],
            },
        },
    )
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    last = data["mensagens"][-1]
    assert last["status"] == "ok" and last["texto"].startswith("Montei o briefing")
    prices = {p["nome"]: p["preco"] for p in data["briefing"]["produtos"]}
    assert prices == {"Panetone": "R$ 19,90", "Chocotone": ""}
    assert data["briefing"]["referencias"] == [ref]
    assert data["briefing"]["formato"] == "story"


def test_interpret_timeout_falls_back_to_rules(queue_app):
    user = UserClient(queue_app, 10)
    conv = new_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Arroz 5kg 24,90 e Feijão 1kg"})
    conn = Conn(queue_app.extensions["sg_studio"]["db"])
    conn.run("UPDATE sg_jobs SET created_at = created_at - 3600")
    conn.commit()
    conn.close()
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    last = data["mensagens"][-1]
    assert last["status"] == "ok" and "regras" not in last["texto"] and "Confira os dados" in last["texto"]
    assert data["briefing"]["produtos"][0]["preco"] == "R$ 24,90"


# ------------------------------------------------------------ SSRF


@pytest.mark.parametrize(
    "url",
    [
        "http://images.openfoodfacts.org/a.jpg",
        "https://evil.example/a.jpg",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pw@images.openfoodfacts.org/a.jpg",
        "https://images.openfoodfacts.org:8443/a.jpg",
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeUrl):
        check_url(url)


def test_allowlisted_host_resolving_to_private_ip_is_blocked(monkeypatch):
    monkeypatch.setattr(security.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 443))])
    with pytest.raises(UnsafeUrl):
        check_url("https://images.openfoodfacts.org/x.jpg")


def test_import_rejects_non_allowlisted_url(user):
    resp = user.post("/studio/api/products/import", json={"image_url": "https://127.0.0.1/x.png"})
    assert resp.status_code == 400



def test_assets_go_to_external_storage_when_configured(app, user, other_user, monkeypatch):
    from studio import storage
    from studio.db import get_conn

    bucket = {}
    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "put", lambda asset_id, data, mime: bucket.setdefault(f"sitegenius-studio/{asset_id}", data) and f"sitegenius-studio/{asset_id}")
    monkeypatch.setattr(storage, "get", lambda key, mime: bucket[key])
    asset_id = upload(user, data=image_bytes("PNG")).get_json()["asset"]["id"]
    with app.app_context():
        row = get_conn().one("SELECT data, storage_key FROM sg_assets WHERE id = ?", (asset_id,))
    assert row["data"] is None and row["storage_key"] == f"sitegenius-studio/{asset_id}"
    resp = user.get(f"/studio/api/assets/{asset_id}")
    assert resp.status_code == 200 and resp.data == bucket[row["storage_key"]]
    assert other_user.get(f"/studio/api/assets/{asset_id}").status_code == 404


def test_storage_failure_falls_back_to_database(app, user, monkeypatch):
    from studio import storage
    from studio.db import get_conn

    def broken(*_):
        raise ConnectionError("cloudinary fora")

    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "put", broken)
    asset_id = upload(user, data=image_bytes("PNG")).get_json()["asset"]["id"]
    with app.app_context():
        row = get_conn().one("SELECT data, storage_key FROM sg_assets WHERE id = ?", (asset_id,))
    assert row["data"] and row["storage_key"] is None
    assert user.get(f"/studio/api/assets/{asset_id}").status_code == 200
