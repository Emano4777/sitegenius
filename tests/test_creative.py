"""Criação livre (free_image): interpretação, perguntas, edição, referências, segurança e tempos."""
import io
import json

import pytest
from PIL import Image

from studio import creative, edits
from studio.db import get_conn
from tests.conftest import UserClient, image_bytes, make_app
from tests.test_studio_api import new_conversation, upload
from tests.test_workers import FakeComfy, FakeOllama, FlaskSite
from workers import acer_agent, hostgator_interpreter


def send(user, conv, texto, anexos=(), opcoes=None):
    resp = user.post(f"/studio/api/conversations/{conv}/messages",
                     json={"texto": texto, "anexos": list(anexos), "opcoes": opcoes or {}})
    assert resp.status_code == 201, resp.data
    return user.get(f"/studio/api/conversations/{conv}").get_json()


def render_all(render_worker, tmp_path, comfy=None):
    comfy = comfy or FakeComfy()
    while True:
        job = render_worker.claim()
        if not job:
            return comfy
        acer_agent.process(render_worker, job, comfy, acer_agent.LocalTimings(str(tmp_path / "t.json")))


# ------------------------------------------------------------ caso (a): cena livre


def test_free_scene_generates_image_directly_without_flyer(user, render_worker, tmp_path):
    conv = new_conversation(user)
    data = send(user, conv, "astronauta em floresta cinematográfica, formato story")
    job = data["jobs"][0]
    assert job["tipo"] == "free_image" and job["estilo"] == "cinema" and job["proporcao"] == "9:16"
    assert data["mensagens"][-1]["job_id"] == job["id"]

    comfy = render_all(render_worker, tmp_path)
    workflow = comfy.calls[0]
    prompt = workflow["5"]["inputs"]["text"]
    assert "astronauta" in prompt and "cinematic" in prompt and "no text" in prompt
    assert (workflow["7"]["inputs"]["width"], workflow["7"]["inputs"]["height"]) == (656, 1168)

    done = user.get(f"/studio/api/jobs/{job['id']}").get_json()["job"]
    assert done["status"] == "done" and done["tempos"]["comfy_s"] == 12.0 and done["duracao_s"] is not None
    img = Image.open(io.BytesIO(user.get(f"/studio/api/assets/{done['resultado_asset_id']}").data))
    assert img.size == (656, 1168)  # entregue direto: sem montagem de encarte (que seria 1080x1920)


def test_fast_quality_uses_smaller_size(user):
    conv = new_conversation(user)
    data = send(user, conv, "um gato astronauta", opcoes={"qualidade": "rapido", "proporcao": "1:1"})
    assert data["jobs"][0]["tamanho"] == [640, 640]


# ------------------------------------------------------------ perguntas só quando indispensável


def test_vague_request_asks_then_go_anyway_generates(user):
    conv = new_conversation(user)
    data = send(user, conv, "faça uma imagem bonita por favor")
    assert not data["jobs"] and "O que você quer ver" in data["mensagens"][-1]["texto"]
    data = send(user, conv, "gere assim mesmo")
    assert len(data["jobs"]) == 1 and data["jobs"][0]["tipo"] == "free_image"


def test_specific_request_never_asks(user):
    conv = new_conversation(user)
    data = send(user, conv, "retrato de uma senhora sorrindo numa padaria")
    assert len(data["jobs"]) == 1


# ------------------------------------------------------------ iteração sobre a imagem anterior


def test_edit_uses_previous_result_as_img2img(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, "farol numa costa rochosa ao entardecer")
    render_all(render_worker, tmp_path)
    first = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]

    data = send(user, conv, "deixe mais escuro, com tempestade")
    edit = data["jobs"][0]
    assert edit["modo"] == "img2img" and edit["id"] != first["id"]
    comfy = render_all(render_worker, tmp_path)
    wf = comfy.calls[0]
    assert "sg_load" in wf and abs(wf["8"]["inputs"]["denoise"] - edits.edit_strength("deixe mais escuro, com tempestade")) < 1e-6
    assert "farol" in wf["5"]["inputs"]["text"] and "tempestade" in wf["5"]["inputs"]["text"]
    sent_image = Image.open(io.BytesIO(comfy.uploads[0][1]))
    assert sent_image.size == tuple(first["tamanho"])  # é mesmo a imagem anterior que foi para o ComfyUI


def test_regenerate_creates_new_seed(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, "dragão de origami sobre a mesa")
    render_all(render_worker, tmp_path)
    job = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]
    resp = user.post(f"/studio/api/jobs/{job['id']}/regenerate")
    assert resp.status_code == 201
    again = resp.get_json()["job"]
    assert again["seed"] != job["seed"] and again["prompt"] == job["prompt"]


# ------------------------------------------------------------ caso (b): anúncio imobiliário com referência


def test_real_estate_ad_with_reference_uses_img2img_and_never_invents_text(user, render_worker, tmp_path):
    conv = new_conversation(user)
    ref = upload(user, kind="reference", data=image_bytes("JPEG", (400, 300))).get_json()["asset"]["id"]
    data = send(user, conv, "anúncio imobiliário desta sala de estar, mais iluminada e elegante", anexos=[ref],
                opcoes={"fidelidade": "alta"})
    job = data["jobs"][0]
    assert job["estilo"] == "imovel" and job["modo"] == "img2img"
    comfy = render_all(render_worker, tmp_path)
    wf = comfy.calls[0]
    assert wf["8"]["inputs"]["denoise"] == creative.REFERENCE_STRENGTH["alta"]
    prompt = wf["5"]["inputs"]["text"]
    assert "no text" in prompt and "R$" not in prompt


def test_quoted_text_is_the_only_text_allowed():
    spec = creative.build_spec(prompt_original='banner com "Inauguração dia 10"', prompt_visual="shop front",
                               style="auto", aspect="16:9", quality="qualidade",
                               user_texts=['banner com "Inauguração dia 10"'])
    # O texto nunca vai para o modelo de imagem: é aplicado depois, com a grafia exata.
    assert "Inauguração" not in spec["prompt"] and "no text" in spec["prompt"]
    assert spec["text_plan"]["blocos"][0]["texto"] == "Inauguração dia 10"


# ------------------------------------------------------------ caso (c): encarte com preço real


def test_prices_switch_conversation_to_flyer_mode(user):
    conv = new_conversation(user)
    data = send(user, conv, "Encarte de hortifruti\nTomate kg 6,99")
    assert data["conversa"]["modo"] == "ad" and not data["jobs"]
    assert any(p["preco"] == "R$ 6,99" for p in data["briefing"]["produtos"])


# ------------------------------------------------------------ HostGator (modelo de linguagem/visão)


def test_model_answer_is_validated_and_images_reach_the_model(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 31)
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    conv = new_conversation(user)
    send(user, conv, "quero essa pessoa como astronauta", anexos=[ref])
    site = FlaskSite(app)
    job = site.call("POST", "/claim", {})["job"]
    assert job["payload"]["mode"] == "creative"
    reply = json.dumps({"acao": "gerar", "resposta": "Vou criar o retrato de astronauta.",
                        "prompt_visual": "portrait of the person from the reference as an astronaut, studio light, detailed suit",
                        "estilo": "inexistente", "proporcao": "99:1", "uso_referencia": "base",
                        "descricao_referencias": "mulher de cabelo curto, fundo claro"})
    ollama = FakeOllama(reply)
    hostgator_interpreter.process(site, ollama, job, vision=True)
    assert len(ollama.messages[-1]["images"]) == 1
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    gen = data["jobs"][0]
    assert gen["estilo"] == "auto" and gen["proporcao"] == creative.DEFAULT_ASPECT  # valores inválidos descartados
    assert gen["modo"] == "img2img" and data["mensagens"][-1]["texto"].startswith("Vou criar")


def test_model_without_vision_is_told_it_cannot_see(tmp_path):
    payload = {"text": "use esta foto", "image_asset_ids": ["x"], "history": [], "styles": ["auto"], "aspects": ["1:1"]}
    messages = hostgator_interpreter.build_creative_messages(payload, [], vision=False)
    assert "NÃO consegue vê-las" in messages[1]["content"] and "images" not in messages[-1]


def test_model_question_and_failure_fallback(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 32)
    conv = new_conversation(user)
    send(user, conv, "algo para o meu negócio")
    site = FlaskSite(app)
    job = site.call("POST", "/claim", {})["job"]
    hostgator_interpreter.process(site, FakeOllama(json.dumps({"acao": "perguntar", "pergunta": "Qual é o seu negócio?"})), job, True)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert not data["jobs"] and data["mensagens"][-1]["texto"].startswith("Qual é o seu negócio?")

    send(user, conv, "uma cafeteria aconchegante com luz quente")
    job = site.call("POST", "/claim", {})["job"]
    with pytest.raises(RuntimeError):
        hostgator_interpreter.process(site, FakeOllama("não é json"), job, True)
    site.call("POST", f"/jobs/{job['id']}/fail", {"erro": "json inválido"})
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert len(data["jobs"]) == 1 and "regras" not in data["mensagens"][-1]["texto"]  # nada técnico para o cliente
    assert data["mensagens"][-1]["texto"].startswith("Entendi sua ideia")


# ------------------------------------------------------------ segurança e multiusuário


def test_users_cannot_use_or_regenerate_other_users_images(user, other_user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, "castelo de areia gigante")
    render_all(render_worker, tmp_path)
    job = user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]
    assert other_user.post(f"/studio/api/jobs/{job['id']}/regenerate").status_code == 404
    assert other_user.get(f"/studio/api/assets/{job['resultado_asset_id']}").status_code == 404
    other_conv = new_conversation(other_user)
    resp = other_user.post(f"/studio/api/conversations/{other_conv}/messages",
                           json={"texto": "use esta", "anexos": [job["resultado_asset_id"]]})
    assert resp.status_code == 403
    assert other_user.get("/studio/api/galeria").get_json()["imagens"] == []
    assert len(user.get("/studio/api/galeria").get_json()["imagens"]) == 1


def test_active_generation_limit(user):
    conv = new_conversation(user)
    send(user, conv, "montanha nevada")
    send(user, conv, "lago azul")
    data = send(user, conv, "deserto vermelho")
    assert len(data["jobs"]) == 2 and "imagens sendo criadas" in data["mensagens"][-1]["texto"]


def test_old_agent_payload_shape_is_rejected_cleanly(user, render_worker):
    """Agente antigo (sem free_image) não pode montar encarte vazio: o payload não tem briefing."""
    conv = new_conversation(user)
    send(user, conv, "floresta encantada")
    job = render_worker.claim()
    assert "briefing" not in job["payload"] and job["payload"]["gen_type"] == "free_image"


# ------------------------------------------------------------ status, presença e estimativas reais


def test_status_and_estimates_come_from_measurements(user, render_worker, tmp_path):
    assert user.get("/studio/api/status").get_json()["gerador_online"] is False
    assert user.get("/studio/api/estimativas").get_json()["tamanhos"] == {}
    conv = new_conversation(user)
    send(user, conv, "barco à vela no pôr do sol", opcoes={"proporcao": "4:5"})
    render_all(render_worker, tmp_path)
    status = user.get("/studio/api/status").get_json()
    assert status["gerador_online"] is True and status["fila"] == 0
    est = user.get("/studio/api/estimativas").get_json()["tamanhos"]["768x960"]
    assert est["amostras"] == 1 and est["mediana_gpu_s"] == 12.0


def test_auto_mode_uses_rules_when_interpreter_offline(app, user):
    assert app.extensions["sg_studio"]["config"].interpreter_mode == "auto"
    assert user.get("/studio/api/me").get_json()["interpretacao"] == "regras"
    with app.app_context():
        get_conn().run("INSERT INTO sg_worker_status (worker_id, kind, last_seen) VALUES ('hostgator', 'interpret', ?)",
                       (__import__("time").time().__int__(),))
    assert user.get("/studio/api/me").get_json()["interpretacao"] == "ia"


def test_model_reply_with_technical_words_is_replaced():
    assert creative.friendly_reply("Vou usar o modelo Qwen com seed 42", "Padrão.") == "Padrão."
    assert creative.friendly_reply("Gerando o prompt na GPU", "Padrão.") == "Padrão."
    assert creative.friendly_reply("Entendi! Vou criar um farol ao entardecer.", "Padrão.") == "Entendi! Vou criar um farol ao entardecer."
