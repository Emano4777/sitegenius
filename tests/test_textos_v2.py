"""Textos sem aspas, perguntas quando falta o texto, superfícies (perspectiva/curva) e leitura visual (OCR)."""
import io
import json
import random

from PIL import Image, ImageDraw, ImageFilter

from studio import creative, ocr, surfaces, textplan, typography
from studio.db import get_conn
from tests.test_creative import render_all, send
from tests.test_studio_api import new_conversation


def png(img):
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def noisy(w, h, seed=1):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (90, 70, 60))
    draw = ImageDraw.Draw(img)
    for _ in range(2500):
        x, y, c = rnd.randrange(w), rnd.randrange(h), rnd.randrange(40, 200)
        draw.ellipse((x, y, x + rnd.randrange(3, 14), y + rnd.randrange(3, 14)), fill=(c, c - 20, c - 40))
    return img


# ------------------------------------------------------------ interpretação sem aspas


def texts(msg):
    return {b["texto"]: b for b in textplan.plan_from_request(msg)["blocos"]}


def test_brand_without_quotes_and_position():
    found = texts("Cria uma arte para minha empresa Emanuel Tecnologia com meu nome no centro.")
    assert list(found) == ["Emanuel Tecnologia"] and found["Emanuel Tecnologia"]["posicao"] == "centro"


def test_big_price_and_commercial_condition_are_literal():
    found = texts("Faz um anúncio de uma casa por 540 mil reais e coloca aceita financiamento.")
    assert found["R$ 540 mil"]["papel"] == "preco" and found["aceita financiamento"]["papel"] == "subtitulo"
    assert creative.classify("Faz um anúncio de uma casa por 540 mil reais") == "free_image"


def test_price_emphasis_and_promo_is_single_piece():
    plan = textplan.plan_from_request("Quero uma promoção de vitamina C por 19,90, com o preço em destaque.")
    assert textplan.plan_texts(plan) == ["R$ 19,90"] and plan["destaque"] == "preco"
    assert creative.classify("Quero uma promoção de vitamina C por 19,90") == "free_image"


def test_title_subtitle_and_cta_without_quotes():
    found = texts("Crie um banner com o título Super Ofertas, subtítulo Até 50% de desconto e coloca Compre já no rodapé")
    assert {k: v["papel"] for k, v in found.items()} == {"Super Ofertas": "titulo", "Até 50% de desconto": "subtitulo",
                                                         "Compre já": "cta"}
    assert found["Compre já"]["posicao"] == "rodape"


def test_name_before_written_on_surface():
    plan = textplan.plan_from_request("Crie uma imagem de um escritório moderno com Emanuel Tecnologia escrito na parede")
    assert textplan.plan_texts(plan) == ["Emanuel Tecnologia"] and plan["superficie"] == "parede"
    assert plan["estilo"] != "tecnologico"  # o nome da empresa não decide o estilo


def test_text_name_never_decides_style_and_brazilian_symbols_survive():
    plan = textplan.plan_from_request('arte com "Açaí & Cia — nº 1 em São João: 2×R$ 9,99!"')
    assert textplan.plan_texts(plan) == ["Açaí & Cia — nº 1 em São João: 2×R$ 9,99!"]
    _, report = typography.compose(png(Image.new("RGB", (880, 880), (20, 40, 80))), plan, use_ocr=False)
    assert report["ok"]


def test_missing_text_asks_and_never_invents(user):
    conv = new_conversation(user)
    data = send(user, conv, "Coloca uma frase de boas-vindas na parte superior de uma foto de praia")
    assert not data["jobs"]
    reply = data["mensagens"][-1]["texto"]
    assert "Seja bem-vindo!" in reply and "pode usar" in reply
    data = send(user, conv, "pode usar")
    job = data["jobs"][0]
    with user.client.application.app_context():
        payload = json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"])
    blocks = payload["spec"]["text_plan"]["blocos"]
    assert blocks == [{"texto": "Seja bem-vindo!", "papel": "subtitulo", "posicao": "topo", "confirmado": True}] or \
        blocks[0]["texto"] == "Seja bem-vindo!"


def test_missing_text_answer_is_used_literally():
    pending = creative.rules_interpretation("arte de cafeteria com uma frase na parte de baixo", {}, None, None, [], None, [])
    assert pending[0] == "perguntar"
    spec = creative.resolve_text_answer("Café quentinho o dia todo", pending[2])
    assert textplan.plan_texts(spec["text_plan"]) == ["Café quentinho o dia todo"]
    assert "Café quentinho" not in spec["prompt"]


# ------------------------------------------------------------ superfícies


def test_sign_in_perspective():
    img = noisy(1088, 720)
    ImageDraw.Draw(img).polygon([(330, 170), (800, 215), (790, 470), (320, 500)], fill=(236, 232, 220))
    plan = textplan.plan_from_request('loja com "Padaria Pão & Café" na placa')
    _, report = typography.compose(png(img), plan, use_ocr=False)
    assert report["superficie"]["modo"] == "perspectiva" and report["ok"]


def test_tshirt_curved():
    img = Image.new("RGB", (880, 880), (70, 110, 140))
    ImageDraw.Draw(img).polygon([(250, 180), (360, 140), (520, 140), (630, 180), (720, 320), (640, 360), (610, 300),
                                 (610, 800), (270, 800), (270, 300), (240, 360), (160, 320)], fill=(245, 245, 240))
    plan = textplan.plan_from_request('camiseta branca com "Emanuel Tecnologia" na camiseta')
    _, report = typography.compose(png(img.filter(ImageFilter.GaussianBlur(1))), plan, use_ocr=False)
    assert report["superficie"]["modo"] == "curva" and report["ok"]


def test_no_confident_surface_falls_back_to_highlight():
    plan = textplan.plan_from_request('cena com "Loja Nova" escrito na parede')
    _, report = typography.compose(png(noisy(880, 880, seed=3)), plan, use_ocr=False)
    assert report["superficie"]["modo"] == "destaque"
    drawn = next(c for c in report["verificacoes"] if c["nome"] == "textos_completos_e_identicos")
    assert drawn["ok"]  # o texto continua presente e idêntico


def test_surface_prompt_asks_for_blank_surface_without_text():
    spec = creative.build_spec(prompt_original="escritório moderno com Emanuel Tecnologia escrito na parede atrás da mesa",
                               prompt_visual="modern office, Emanuel Tecnologia written on the wall", style="auto",
                               aspect="16:9", quality="qualidade", user_texts=[])
    assert "blank" in spec["prompt"] and "Emanuel" not in spec["prompt"]


# ------------------------------------------------------------ leitura visual (comparação)


def test_ocr_comparison_accepts_missing_accents_and_flags_errors():
    good = [{"texto": "Vera0 2026", "confianca": 0.94, "caixa": [0, 0, 1, 1]}]
    assert ocr.compare(["Verão 2026"], good)["ok"]
    bad = [{"texto": "Emanel Tecnolgia", "confianca": 0.95, "caixa": [0, 0, 1, 1]}]
    result = ocr.compare(["Emanuel Tecnologia"], bad)
    assert result["textos"][0]["status"] != "ok" and not result["ok"]
    price = [{"texto": "RS", "confianca": 0.97, "caixa": [0, 0, 1, 1]}, {"texto": "199", "confianca": 1, "caixa": [0, 0, 1, 1]},
             {"texto": ".90", "confianca": 0.9, "caixa": [0, 0, 1, 1]}]
    assert ocr.compare(["R$ 199,90"], price)["textos"][0]["numeros_ok"]
    assert not ocr.compare(["R$ 199,90"], [{"texto": "R$ 189,90", "confianca": 0.99, "caixa": [0, 0, 1, 1]}])["ok"]


def test_ocr_flags_unrequested_text():
    items = [{"texto": "Emanuel Tecnologia", "confianca": 0.99, "caixa": [0, 0, 1, 1]},
             {"texto": "SALE OFF", "confianca": 0.93, "caixa": [0, 0, 1, 1]}]
    result = ocr.compare(["Emanuel Tecnologia"], items)
    assert [e["texto"] for e in result["indesejados"]] == ["SALE OFF"] and not result["ok"]


# ------------------------------------------------------------ agente v2.3 compõe no Acer


def test_agent_composes_locally_and_server_keeps_clean_art(user, render_worker, tmp_path):
    conv = new_conversation(user)
    data = send(user, conv, 'post quadrado com "Emanuel Tecnologia" no topo')
    render_all(render_worker, tmp_path)
    job = user.get(f"/studio/api/jobs/{data['jobs'][0]['id']}").get_json()["job"]
    assert job["status"] == "done"
    with user.client.application.app_context():
        result = json.loads(get_conn().one("SELECT result FROM sg_jobs WHERE id = ?", (job["id"],))["result"])
        art_kind = get_conn().one("SELECT kind FROM sg_assets WHERE id = ?", (result["art_asset_id"],))["kind"]
    assert result["composto_no"] == "acer" and art_kind == "art" and result["validacao_texto"]["ok"]
    assert surfaces.kind_of("parede") == "parede"


# ------------------------------------------------------------ o modelo interpreta: ajuste ou criação nova


def test_model_decides_adjustment_vs_new_and_server_validates(tmp_path):
    from tests.conftest import RENDER_SECRET, FlaskSession, UserClient, make_app
    from tests.test_workers import FakeOllama, FlaskSite
    from studio.worker_client import WorkerClient
    from workers import hostgator_interpreter

    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 41)
    worker = WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))
    site = FlaskSite(app)
    conv = new_conversation(user)

    def model(text, answer):
        user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": text, "anexos": [], "opcoes": {}})
        job = site.call("POST", "/claim", {})["job"]
        hostgator_interpreter.process(site, FakeOllama(json.dumps(answer)), job, vision=False)
        return user.get(f"/studio/api/conversations/{conv}").get_json()

    def plan_of(job_id):
        with app.app_context():
            return json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job_id,))["payload"])

    base = {"acao": "gerar", "resposta": "Vou criar.", "prompt_visual": "modern tech office, calm wall, soft light",
            "estilo": "auto", "proporcao": "1:1"}
    data = model('post com "Emanuel Tecnologia" no topo e "Soluções em TI" no rodapé', {**base, "tipo_pedido": "nova"})
    first = data["jobs"][0]
    render_all(worker, tmp_path)

    # 1) Sem frase pronta: "ficou pequeno" -> o modelo diz que é ajuste só de texto -> mesma arte, nada é gerado.
    data = model("o nome ficou meio pequeno", {**base, "tipo_pedido": "ajuste", "ajuste": {
        "cena": False, "textos": True, "operacoes_texto": [{"acao": "aumentar", "papel": "titulo"}]}})
    edit = data["jobs"][0]
    assert edit["tipo"] == "text_edit" and edit["status"] == "done" and worker.claim() is None
    blocks = plan_of(edit["id"])["spec"]["text_plan"]["blocos"]
    assert [b["texto"] for b in blocks] == ["Emanuel Tecnologia", "Soluções em TI"] and blocks[0]["escala"] == 1.3

    # 2) Cena + texto: arte nova a partir da anterior (força pequena), texto trocado literal, texto inventado recusado.
    data = model("deixa o fundo azulado e troca o rodapé para Suporte 24h", {**base, "tipo_pedido": "ajuste",
                 "prompt_visual": "modern tech office at dusk, deep blue tones, calm wall", "ajuste": {
        "cena": True, "textos": True, "intensidade": "pequena", "operacoes_texto": [
            {"acao": "trocar", "alvo": "Soluções em TI", "texto": "Suporte 24h"},
            {"acao": "adicionar", "texto": "Ligue já", "papel": "cta"}]}})
    job = data["jobs"][0]
    payload = plan_of(job["id"])
    assert payload["render_mode"] == "img2img" and payload["denoise"] == 0.55
    assert textplan.plan_texts(payload["spec"]["text_plan"]) == ["Emanuel Tecnologia", "Suporte 24h"]
    render_all(worker, tmp_path)

    # 3) Pedido novo: não herda textos nem usa a arte anterior.
    data = model('agora uma arte de pizzaria com "Pizza Boa"', {**base, "tipo_pedido": "nova"})
    payload = plan_of(data["jobs"][0]["id"])
    assert payload["render_mode"] == "txt2img" and textplan.plan_texts(payload["spec"]["text_plan"]) == ["Pizza Boa"]
    assert first["id"] != data["jobs"][0]["id"]


def test_guard_when_model_wrongly_says_new(tmp_path):
    """Modelo pequeno às vezes erra: "o nome ficou pequeno" classificado como nova. A peça não pode ser perdida."""
    from tests.conftest import RENDER_SECRET, FlaskSession, UserClient, make_app
    from tests.test_workers import FakeOllama, FlaskSite
    from studio.worker_client import WorkerClient
    from workers import hostgator_interpreter

    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 42)
    worker = WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))
    site = FlaskSite(app)
    conv = new_conversation(user)
    base = {"acao": "gerar", "resposta": "Ok.", "prompt_visual": "modern office, calm wall", "estilo": "auto",
            "proporcao": "1:1", "tipo_pedido": "nova"}
    for text in ('post com "Emanuel Tecnologia" no topo', "o nome ficou meio pequeno"):
        user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": text, "anexos": [], "opcoes": {}})
        job = site.call("POST", "/claim", {})["job"]
        hostgator_interpreter.process(site, FakeOllama(json.dumps(base)), job, vision=False)
        render_all(worker, tmp_path)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert data["jobs"][0]["tipo"] == "text_edit"


def test_real_model_answers_are_accepted():
    """Respostas reais do gemma4 (02/11/2026): acao "ajustar" e cena marcada sem mudar a descrição."""
    prev = {"prompt_visual": "A sleek, modern office environment with large windows, soft daylight", "aspect_ratio": "1:1",
            "text_plan": {"versao": 1, "estilo": "moderno", "efeito": "auto", "cor": None, "blocos": [
                {"texto": "Emanuel Tecnologia", "papel": "titulo", "posicao": "auto"},
                {"texto": "Suporte 24 horas", "papel": "subtitulo", "posicao": "rodape"}]}}
    answer = {"acao": "ajustar", "resposta": "Vou deixar as letras douradas.", "editar_anterior": True,
              "prompt_visual": prev["prompt_visual"], "tipo_pedido": "ajuste",
              "ajuste": {"cena": True, "textos": True, "intensidade": "pequena", "operacoes_texto": [
                  {"acao": "cor", "valor": "dourado", "todos": True}]}}
    acao, _, spec = creative.normalize_model_answer(answer, "e se as letras fossem douradas?", {}, prev, "asset1", [],
                                                    ["e se as letras fossem douradas?"])
    assert acao == "ajustar_texto" and spec["text_plan"]["cor"] == [212, 175, 55]
    answer = {**answer, "resposta": "Ok", "ajuste": {"cena": False, "textos": True, "operacoes_texto": [
        {"acao": "aumentar", "alvo": "Emanuel Tecnologia"}]}}
    acao, _, spec = creative.normalize_model_answer(answer, "o nome ficou meio pequeno", {}, prev, "asset1", [], [])
    assert acao == "ajustar_texto" and spec["text_plan"]["blocos"][0]["escala"] == 1.3


# ------------------------------------------------------------ reprodução do defeito de produção (02/11/2026)


def test_production_bug_capital_t_and_realistic_background(tmp_path):
    """Conversa real: o modelo pediu 'mudar' para 'Tecnologia é aqui' e o servidor descartava; o fundo mal mudava;
    'Você não mudou nada' repetia a mesma imagem. Respostas do modelo copiadas do banco de produção."""
    from tests.conftest import RENDER_SECRET, FlaskSession, UserClient, make_app
    from tests.test_workers import FakeOllama, FlaskSite
    from studio.worker_client import WorkerClient
    from workers import hostgator_interpreter

    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 43)
    worker = WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))
    site = FlaskSite(app)
    conv = new_conversation(user)

    def say(text, answer=None):
        user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": text, "anexos": [], "opcoes": {}})
        if answer is not None:
            job = site.call("POST", "/claim", {})["job"]
            hostgator_interpreter.process(site, FakeOllama(json.dumps(answer)), job, vision=False)
        return user.get(f"/studio/api/conversations/{conv}").get_json()

    def payload(job_id):
        with app.app_context():
            return json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job_id,))["payload"])

    abstract = ("A sleek, futuristic digital landscape representing advanced technology. Abstract glowing circuits and "
                "data streams flow across a dark, high-tech background.")
    say("Cria uma arte bem legal escrita tecnologia é aqui representando tecnologia e tals",
        {"acao": "gerar", "tipo_pedido": "nova", "resposta": "Entendi!", "prompt_visual": abstract, "estilo": "auto",
         "proporcao": "1:1", "textos": [{"texto": "tecnologia é aqui", "papel": "titulo", "posicao": "centro"}]})
    render_all(worker, tmp_path)

    data = say("Coloca o t de tecnologia é aqui maiúsculo por favor e muda o fundo não gostei coloca algo mais realista",
               {"acao": "ajustar", "tipo_pedido": "ajuste", "editar_anterior": True, "resposta": "Fechou!",
                "prompt_visual": abstract.replace("futuristic digital", "realistic modern") + " Editing previous image.",
                "estilo": "auto", "proporcao": "1:1",
                "ajuste": {"cena": True, "textos": True, "intensidade": "media", "operacoes_texto": [
                    {"acao": "mudar", "alvo": "Tecnologia é aqui", "papel": "titulo", "texto": "Tecnologia é aqui",
                     "valor": "T maiúsculo"}]}})
    job = data["jobs"][0]
    p = payload(job["id"])
    assert textplan.plan_texts(p["spec"]["text_plan"]) == ["Tecnologia é aqui"]  # T maiúsculo de verdade
    assert p["render_mode"] == "txt2img" and p["spec"]["style"] == "foto"  # cenário novo, não img2img fraco
    assert "photorealistic" in p["prompt"]
    reply = data["mensagens"][-1]["texto"]
    assert "“tecnologia é aqui” → “Tecnologia é aqui”" in reply and "fundo totalmente novo" in reply
    render_all(worker, tmp_path)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    # O gerador de teste devolve sempre a mesma imagem: a conferência visual precisa perceber e avisar (não fingir).
    assert "O fundo mudou pouco" in data["mensagens"][-1]["texto"]

    # "Você não mudou nada": recupera o pedido, não repete a mesma imagem e não finge.
    data = say("Você não mudou nada")
    retry = data["jobs"][0]
    assert retry["id"] != job["id"] and retry["tipo"] == "free_image"
    p2 = payload(retry["id"])
    assert textplan.plan_texts(p2["spec"]["text_plan"]) == ["Tecnologia é aqui"]
    assert p2["render_mode"] == "txt2img" and p2["seed"] != p["seed"]
    assert data["mensagens"][-1]["texto"].startswith("Você tem razão")


def test_text_only_requests_never_regenerate(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, 'post com "promoção de verão" no topo')
    render_all(render_worker, tmp_path)
    for text, expected in [("deixa tudo maiúsculo", "PROMOÇÃO DE VERÃO"), ("agora tudo minúsculo", "promoção de verão"),
                           ("coloca a primeira letra maiúscula", "Promoção de verão")]:
        data = send(user, conv, text)
        job = data["jobs"][0]
        assert job["tipo"] == "text_edit" and render_worker.claim() is None, text
        with user.client.application.app_context():
            plan = json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"])
        assert textplan.plan_texts(plan["spec"]["text_plan"]) == [expected], text

