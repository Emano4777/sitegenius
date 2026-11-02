"""Desempenho: modelo mantido entre tarefas, progresso real por etapa/passo, perfis e benchmarks."""
import json

import pytest

from studio import creative
from tests.conftest import RENDER_SECRET
from tests.test_creative import render_all, send
from tests.test_studio_api import new_conversation
from tests.test_workers import FakeComfy
from workers import acer_agent

TEMPLATE = json.load(open("workers/comfyui_workflow.example.json", encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_profiles(monkeypatch):
    monkeypatch.delenv("SG_RENDER_PROFILES", raising=False)


# ------------------------------------------------------------ agente: passos e memória


def test_set_steps_changes_all_samplers_and_keeps_template():
    filled = acer_agent.fill_workflow(TEMPLATE, "x", 1, 640, 640)
    assert acer_agent.workflow_steps(acer_agent.set_steps(json.loads(json.dumps(filled)), 6)) == 6
    assert acer_agent.workflow_steps(acer_agent.set_steps(filled, None)) == TEMPLATE["8"]["inputs"]["steps"]


def test_agent_keeps_model_loaded_unless_ram_is_low(monkeypatch):
    comfy, calls = FakeComfy(), []
    comfy.free_memory = lambda: calls.append("free")
    monkeypatch.delenv("SG_COMFY_FREE_AFTER_JOB", raising=False)
    assert comfy.after_job() == "modelo mantido" and calls == []  # 8000 MB livres
    comfy.memory = lambda: {"ram_livre_mb": 800.0}
    assert comfy.after_job().startswith("liberado") and calls == ["free"]
    monkeypatch.setenv("SG_COMFY_FREE_AFTER_JOB", "1")
    comfy.memory = lambda: {"ram_livre_mb": 8000.0}
    comfy.after_job()
    assert calls == ["free", "free"]


# ------------------------------------------------------------ progresso real (WebSocket do ComfyUI)


def feed_run(tracker, cached=()):
    events = [
        (100.0, {"type": "execution_start", "data": {"prompt_id": "p"}}),
        (100.0, {"type": "execution_cached", "data": {"nodes": list(cached), "prompt_id": "p"}}),
        (100.0, {"type": "executing", "data": {"node": "5", "prompt_id": "p"}}),   # CLIPTextEncode
        (112.0, {"type": "executing", "data": {"node": "8", "prompt_id": "p"}}),   # KSampler
    ]
    for step in range(1, 10):
        events.append((112.0 + 20 * step, {"type": "progress", "data": {"value": step, "max": 9, "prompt_id": "p"}}))
    events += [
        (292.0, {"type": "executing", "data": {"node": "9", "prompt_id": "p"}}),   # VAEDecode
        (310.0, {"type": "executing", "data": {"node": "10", "prompt_id": "p"}}),  # SaveImage
        (311.0, {"type": "executing", "data": {"node": None, "prompt_id": "p"}}),
        (311.0, {"type": "execution_success", "data": {"prompt_id": "p"}}),
    ]
    for t, message in events:
        tracker.feed(message, now=t)


def test_tracker_measures_each_stage_and_detects_cached_model():
    tracker = acer_agent.ProgressTracker(acer_agent.fill_workflow(TEMPLATE, "x", 1, 640, 640), clock=lambda: 99.0)
    tracker.prompt_id = "p"
    feed_run(tracker, cached=["1", "2", "3", "4"])  # loaders em cache = modelo NÃO recarregado
    t = tracker.timings()
    assert t["encode_s"] == 12.0 and t["sampling_s"] == 180.0 and t["vae_s"] == 18.0
    assert t["steps"] == 9 and t["s_por_passo"] == 20.0 and t["modelo_em_cache"] == 1
    assert t["preparo_amostragem_s"] == 20.0  # do início do KSampler até o 1º passo (recarga do modelo na VRAM)
    assert "load_s" not in t


def test_tracker_ignores_other_prompts():
    tracker = acer_agent.ProgressTracker({"8": {"class_type": "KSampler", "inputs": {}}})
    tracker.prompt_id = "meu"
    tracker.feed({"type": "progress", "data": {"value": 5, "max": 9, "prompt_id": "outro"}}, now=1)
    assert tracker.snapshot(now=2)["step"] == 0


def test_progress_message_uses_real_step_rate():
    snap = {"stage": "sampling", "step": 3, "max": 9, "durations": {"sampling": 60.0}}
    pct, message = acer_agent.progress_message(snap, 80, None, expected_vae=18)
    assert message == "Gerando: passo 3/9 · ~138s restantes" and 25 < pct < 90  # 6 passos x 20 s + VAE 18 s
    assert acer_agent.progress_message({"stage": "load", "durations": {}}, 5, None, None)[1] == "Carregando o modelo na GPU"
    assert "média medida" in acer_agent.progress_message(None, 50, 200, None)[1]


def test_ticker_throttles_reports():
    sent = []
    tick = acer_agent.ticker(lambda pct, msg, timings=None, info=None: sent.append((msg, info)), None, None)
    for second in range(10):
        tick(second, {"stage": "sampling", "step": second, "max": 9, "durations": {"sampling": second * 2.0}})
    assert 1 < len(sent) <= 4  # muda a cada segundo, mas envia no máximo a cada 4 s
    assert sent[-1][1]["etapa"] == "sampling" and sent[-1][1]["total"] == 9 and "restante_s" in sent[-1][1]


# ------------------------------------------------------------ perfis Rápido / Qualidade


def test_profiles_from_env_control_size_and_steps(monkeypatch):
    assert creative.size_for("1:1", "rapido") == [640, 640] and creative.steps_for("rapido") is None
    monkeypatch.setenv("SG_RENDER_PROFILES", json.dumps({"rapido": {"megapixels": 0.3, "steps": 5},
                                                        "qualidade": {"megapixels": 0.78, "steps": 9}}))
    w, h = creative.size_for("16:9", "rapido")
    assert w % 16 == 0 and h % 16 == 0 and w * h <= 300_000 and abs(w / h - 16 / 9) < 0.05
    assert creative.steps_for("rapido") == 5 and creative.steps_for("qualidade") == 9
    monkeypatch.setenv("SG_RENDER_PROFILES", json.dumps({"rapido": {"megapixels": 9, "steps": 500}}))
    assert creative.size_for("1:1", "rapido") == [640, 640] and creative.steps_for("rapido") is None  # inválido ignorado


def test_steps_reach_the_workflow(user, render_worker, tmp_path, monkeypatch):
    monkeypatch.setenv("SG_RENDER_PROFILES", json.dumps({"rapido": {"steps": 5}}))
    conv = new_conversation(user)
    data = send(user, conv, "um farol na neblina", opcoes={"qualidade": "rapido"})
    assert data["jobs"][0]["passos"] == 5 and data["jobs"][0]["qualidade"] == "rapido"
    comfy = render_all(render_worker, tmp_path)
    assert acer_agent.workflow_steps(comfy.calls[0]) == 5
    me = user.get("/studio/api/me").get_json()
    assert me["perfis"]["rapido"]["passos"] == 5 and me["perfis"]["qualidade"]["tamanhos"]["4:5"] == [768, 960]


def test_job_view_separates_queue_wait_from_generation(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, "girassóis ao vento")
    render_all(render_worker, tmp_path)
    job = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]
    assert job["espera_s"] is not None and job["geracao_s"] is not None
    assert job["tempos"]["comfy_s"] == 12.0


# ------------------------------------------------------------ benchmarks do Acer -> estimativas


def test_benchmarks_feed_estimates_until_real_jobs_exist(user, render_worker, interpret_worker):
    rows = [
        {"tamanho": "640x640", "passos": 9, "execucao": 1, "comfy_s": 231.5, "load_s": 40.0},   # fria
        {"tamanho": "640x640", "passos": 9, "execucao": 2, "comfy_s": 150.0, "modelo_em_cache": 1},
        {"tamanho": "640x640", "passos": 9, "execucao": 3, "comfy_s": 160.0, "modelo_em_cache": 1},
        {"tamanho": "../etc", "comfy_s": 1},                                                    # inválido
    ]
    assert render_worker.report_benchmarks(rows)["aceitas"] == 3
    est = user.get("/studio/api/estimativas").get_json()["tamanhos"]["640x640"]
    assert est["fonte"] == "benchmark" and est["amostras"] == 2 and est["mediana_gpu_s"] in (150.0, 160.0)
    with pytest.raises(Exception):
        interpret_worker.report_benchmarks(rows)  # só o worker de render pode enviar


def test_benchmark_endpoint_requires_signature(app):
    resp = app.test_client().post("/studio/worker/benchmarks", json={"linhas": []})
    assert resp.status_code == 401
    assert RENDER_SECRET  # segredo existe só no worker


# ------------------------------------------------------------ v2.2: codificador, progresso estruturado, linguagem


def test_clip_device_only_applied_when_node_supports_it():
    comfy = FakeComfy()
    comfy._inputs = {"CLIPLoaderGGUF": {"clip_name", "type", "device"}}
    wf = comfy.build("x", 1, 640, 640, clip_device="cpu")
    assert wf["2"]["inputs"]["device"] == "cpu"
    comfy._inputs = {"CLIPLoaderGGUF": {"clip_name", "type"}}
    assert "device" not in comfy.build("x", 1, 640, 640, clip_device="cpu")["2"]["inputs"]
    assert "device" not in comfy.build("x", 1, 640, 640)["2"]["inputs"]  # padrão não mexe


def test_friendly_messages_follow_real_stage(user, render_worker):
    conv = new_conversation(user)
    send(user, conv, "um veleiro no mar")
    job_id = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]["id"]
    msg = lambda: user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]["mensagem"]  # noqa: E731
    assert msg() == "Sua imagem vai começar em instantes…"
    job = render_worker.claim()
    assert msg().startswith("Entendi sua ideia!")
    render_worker.progress(job["id"], 30, "Gerando: passo 2/9 (técnico)", info={"etapa": "sampling", "passo": 2, "total": 9})
    assert msg() == "Estou criando os detalhes da sua imagem…"
    render_worker.progress(job["id"], 40, "passo 3/9", info={"etapa": "sampling", "passo": 3, "total": 9, "restante_s": 95})
    assert user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]["restante_s"] == 95  # cálculo real do agente
    render_worker.progress(job["id"], 70, "passo 7/9", info={"etapa": "sampling", "passo": 7, "total": 9})
    assert msg() == "Ajustando iluminação, cores e composição…"
    render_worker.progress(job["id"], 93, "VAE", info={"etapa": "vae"})
    assert msg() == "Sua imagem está quase pronta!"
    render_worker.fail(job["id"], "RuntimeError: ComfyUI CUDA out of memory")
    view = user.get(f"/studio/api/jobs/{job_id}").get_json()["job"]
    assert view["mensagem"] == view["erro"] == "Não consegui concluir sua imagem desta vez. Vamos tentar novamente?"
    assert "CUDA" not in str(view)  # o erro técnico não vai para o cliente


def test_estimate_only_with_enough_real_samples(user, render_worker, tmp_path):
    conv = new_conversation(user)
    for i in range(2):
        send(user, conv, f"uma ponte de madeira número {i}")
        render_all(render_worker, tmp_path)
    send(user, conv, "um lago azul entre montanhas")
    pending = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]
    assert pending["status"] == "queued" and pending["estimativa_s"] is None  # só 2 medições: sem estimativa
    render_all(render_worker, tmp_path)
    send(user, conv, "um deserto vermelho ao amanhecer")
    pending = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]
    assert pending["estimativa_s"] is not None  # 3 medições reais


FORBIDDEN = ["hostgator", "vercel", "acer", "comfy", "ollama", "qwen", "z-image", "gemma", "gpu", "vram", "cpu", "vae",
             "token", "endpoint", "api ", "worker", "polling", "inferência", "benchmark", "seed", "prompt usado",
             "modo regras", "gerador online", "gerador offline", "cache", "passo "]


def test_client_screens_have_no_technical_terms():
    """Varre os textos visíveis das telas do cliente (HTML e strings do JS)."""
    import re as _re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "studio"
    files = [root / "templates/studio/index.html", root / "templates/studio/auth.html",
             root / "static/studio.js", root / "static/auth.js"]
    offenders = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".html":
            visible = _re.sub(r"<[^>]+>", " ", _re.sub(r"\{[%{].*?[%}]\}", " ", text))
            chunks = [visible] + _re.findall(r'(?:placeholder|title|aria-label|alt)="([^"]*)"', text)
        else:
            pairs = _re.findall(r'"([^"\n]*)"|`([^`]*)`', text)
            chunks = [part for pair in pairs for part in pair if part]
            chunks = [c for c in chunks if " " in c or c[:1].isupper()]  # frases, não chaves/ids
        for chunk in chunks:
            low = chunk.lower()
            for word in FORBIDDEN:
                if word in low:
                    offenders.append(f"{f.name}: {word!r} em {chunk.strip()[:80]!r}")
    assert offenders == []
