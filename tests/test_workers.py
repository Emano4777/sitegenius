import io
import json
import secrets
import time

import pytest
from PIL import Image

from studio import security
from tests.conftest import INTERPRET_SECRET, UserClient, image_bytes, make_app
from tests.test_studio_api import new_conversation, prepared_conversation, upload
from workers import acer_agent, hostgator_interpreter


class FakeComfy(acer_agent.Comfy):
    """ComfyUI simulado: usa o workflow de exemplo real (build/img2img) e só finge a execução."""

    def __init__(self):
        self.template = json.load(open("workers/comfyui_workflow.example.json", encoding="utf-8"))
        self.calls, self.uploads = [], []

    def memory(self):
        return {"vram_livre_mb": 5000.0, "ram_livre_mb": 8000.0}

    def upload_image(self, data, name):
        self.uploads.append((name, data))
        return name

    def run(self, workflow, on_tick):
        self.calls.append(workflow)
        on_tick(25)  # dispara um heartbeat
        latent = next((v for v in workflow.values() if v["class_type"] in acer_agent.LATENT_NODES), None)
        scale = workflow.get("sg_scale")
        width, height = (latent or scale)["inputs"]["width"], (latent or scale)["inputs"]["height"]
        return image_bytes("PNG", (width, height), (20, 90, 160)), {"queue_comfy_s": 0.5, "comfy_s": 12.0, "fetch_s": 0.1}

    def free_memory(self):
        pass


def sampler(workflow):
    return next(v for v in workflow.values() if v["class_type"] == "KSampler")


def test_acer_agent_processes_job_end_to_end(user, render_worker, tmp_path):
    conv, _, _ = prepared_conversation(user)
    job_id = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()["job"]["id"]
    comfy = FakeComfy()
    job = render_worker.claim()
    acer_agent.process(render_worker, job, comfy, acer_agent.LocalTimings(str(tmp_path / "t.json")))

    workflow = comfy.calls[0]
    latent = workflow["7"]["inputs"]
    assert (latent["width"], latent["height"]) == (832, 832) and "no text" in workflow["5"]["inputs"]["text"]
    status = user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]
    assert status["status"] == "done"
    result = user.get(f"/studio/api/assets/{status['resultado_asset_id']}")
    assert Image.open(io.BytesIO(result.data)).size == (1080, 1080)


def test_acer_agent_stops_when_canceled(user, render_worker):
    conv, _, _ = prepared_conversation(user)
    job_id = user.post(f"/studio/api/conversations/{conv}/jobs").get_json()["job"]["id"]
    job = render_worker.claim()
    user.post(f"/studio/api/jobs/{job_id}/cancel")
    with pytest.raises(acer_agent.JobCanceled):
        acer_agent.process(render_worker, job, FakeComfy())


def test_img2img_transform_uses_reference_and_denoise():
    template = json.load(open("workers/comfyui_workflow.example.json", encoding="utf-8"))
    filled = acer_agent.fill_workflow(template, "cena", 7, 768, 960)
    wf = acer_agent.to_img2img(filled, "ref.png", 0.6)
    assert "7" not in wf  # latente vazio removido
    assert wf["sg_load"]["inputs"]["image"] == "ref.png"
    assert (wf["sg_scale"]["inputs"]["width"], wf["sg_scale"]["inputs"]["height"]) == (768, 960)
    assert wf["sg_encode"]["inputs"]["vae"] == filled["9"]["inputs"]["vae"]
    assert sampler(wf)["inputs"]["latent_image"] == ["sg_encode", 0] and sampler(wf)["inputs"]["denoise"] == 0.6
    assert filled["8"]["inputs"]["denoise"] == 1  # o original não é alterado


def test_history_seconds_reads_comfy_timestamps():
    entry = {"status": {"messages": [["execution_start", {"timestamp": 1000_000}],
                                     ["execution_success", {"timestamp": 1042_500}]]}}
    start, execution = acer_agent.history_seconds(entry)
    assert start == 1000.0 and execution == 42.5


def test_workflow_placeholders_are_typed():
    template = json.load(open("workers/comfyui_workflow.example.json", encoding="utf-8"))
    filled = acer_agent.fill_workflow(template, 'cena "x"', 123, 768, 960)
    assert filled["5"]["inputs"]["text"] == 'cena "x"'
    assert filled["7"]["inputs"]["width"] == 768 and filled["7"]["inputs"]["height"] == 960
    assert filled["8"]["inputs"]["seed"] == 123
    assert "{{" not in json.dumps(filled)


def test_comfy_must_be_loopback():
    with pytest.raises(SystemExit):
        acer_agent.loopback_only("http://192.168.0.10:8188")
    assert acer_agent.loopback_only("http://127.0.0.1:8188/") == "http://127.0.0.1:8188"


# ------------------------------------------------------------ interpretador HostGator


def test_interpreter_signature_matches_server():
    secret, ts, nonce, body = b"k" * 40, str(int(time.time())), secrets.token_hex(16), b'{"a":1}'
    assert hostgator_interpreter.sign(secret, ts, nonce, "POST", "/studio/worker/claim", body) == security.sign(
        secret, ts, nonce, "POST", "/studio/worker/claim", body
    )


class FlaskSite(hostgator_interpreter.SiteGenius):
    """SiteGenius do interpretador, mas falando com o app de teste."""

    def __init__(self, app):
        super().__init__("http://localhost", "hostgator", INTERPRET_SECRET)
        self.client = app.test_client()

    def call(self, method, path, payload=None, raw=False):
        full_path = "/studio/worker" + path
        body = json.dumps(payload).encode() if payload is not None else b""
        ts, nonce = str(int(time.time())), secrets.token_hex(16)
        headers = {
            "X-SG-Worker": self.worker_id,
            "X-SG-Timestamp": ts,
            "X-SG-Nonce": nonce,
            "X-SG-Signature": hostgator_interpreter.sign(self.secret, ts, nonce, method, full_path, body),
            "Content-Type": "application/json",
        }
        resp = self.client.open(full_path, method=method, data=body, headers=headers)
        assert resp.status_code < 400, resp.data
        if resp.status_code == 204:
            return None
        return resp.data if raw else resp.get_json()


class FakeOllama:
    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat(self, messages, **options):
        self.messages, self.options = messages, options
        return self.reply


def test_interpreter_process_with_vision(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 21)
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    conv = new_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Story de bebidas: Guaraná 2L 7,99 e Água 500ml", "anexos": [ref]})

    site = FlaskSite(app)
    job = site.call("POST", "/claim", {})["job"]
    reply = json.dumps({
        "resposta": "Pronto! Fundo azul gelado como na referência.",
        "briefing": {"formato": "story", "tema": "bebidas", "cenario": "icy blue background",
                     "produtos": [{"nome": "Guaraná 2L", "preco": "7,99"}, {"nome": "Água 500ml", "preco": "1,99"}]},
    })
    ollama = FakeOllama(reply)
    hostgator_interpreter.process(site, ollama, job, vision=True)

    last_user = ollama.messages[-1]
    assert last_user["role"] == "user" and len(last_user["images"]) == 1
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    prices = {p["nome"]: p["preco"] for p in data["briefing"]["produtos"]}
    assert prices == {"Guaraná 2L": "R$ 7,99", "Água 500ml": ""}  # preço inventado descartado
    assert data["mensagens"][-1]["texto"].startswith("Pronto!")


def test_interpreter_failure_falls_back_to_rules(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 22)
    conv = new_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "Arroz 5kg 24,90 e Feijão 1kg"})
    site = FlaskSite(app)
    job = site.call("POST", "/claim", {})["job"]
    with pytest.raises(RuntimeError):
        hostgator_interpreter.process(site, FakeOllama("isto não é json"), job, vision=False)
    site.call("POST", f"/jobs/{job['id']}/fail", {"erro": "modelo não retornou JSON válido"})
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert data["mensagens"][-1]["status"] == "ok"
    assert data["briefing"]["produtos"][0]["preco"] == "R$ 24,90"


def test_worker_client_sends_vercel_bypass_only_when_configured(monkeypatch):
    from studio.worker_client import WorkerClient

    client = WorkerClient("https://exemplo.vercel.app", "acer", "s" * 32)
    monkeypatch.delenv("SG_VERCEL_BYPASS", raising=False)
    assert "x-vercel-protection-bypass" not in client.headers("GET", "/studio/worker/claim", b"")
    monkeypatch.setenv("SG_VERCEL_BYPASS", "token-teste")
    assert client.headers("GET", "/studio/worker/claim", b"")["x-vercel-protection-bypass"] == "token-teste"
