"""Textos sem aspas, perguntas quando falta o texto, superfícies (perspectiva/curva) e leitura visual (OCR)."""
import io
import json
import random

from PIL import Image, ImageDraw, ImageFilter

from studio import creative, edits, ocr, surfaces, textplan, typography
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
    assert payload["render_mode"] == "img2img" and payload["denoise"] == edits.LEVEL_STRENGTH["pequena"]
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
    # Com o acabamento, o título já nasceu "Tecnologia é aqui" na criação; o pedido de T maiúsculo já está atendido.
    assert "fundo totalmente novo" in reply and "Não consegui" not in reply
    render_all(worker, tmp_path)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    # Nada é dado como conferido antes do veredito visual.
    assert data["mensagens"][-1]["status"] == "pending" and "Conferindo" in data["mensagens"][-1]["texto"]
    # O gerador de teste devolve a mesma imagem: o avaliador reprova -> uma nova tentativa com mais liberdade.
    verdict = {"atendeu": False, "nota": 2, "mudou": [], "faltou": ["o fundo continua igual"],
               "resumo": "O fundo ficou praticamente igual."}
    first = site.call("POST", "/claim", {})["job"]  # conferência da criação (aprovada)
    hostgator_interpreter.process(site, FakeOllama(json.dumps({"atendeu": True, "nota": 8})), first, vision=True)
    ev = site.call("POST", "/claim", {})["job"]
    assert ev["payload"]["mode"] == "evaluate" and ev["payload"]["after_id"] and ev["payload"]["before_id"]
    hostgator_interpreter.process(site, FakeOllama(json.dumps(verdict)), ev, vision=True)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    msg = data["mensagens"][-1]["texto"]
    assert "ainda não ficou" in msg and "o fundo continua igual" in msg and "Conferi o resultado:" not in msg


def test_creative_agent_plan_tools_and_honest_evaluation(tmp_path):
    """Defeito da logo (02/11/2026): pedidos de mudança grande viravam retoque fraco e a resposta fingia a mudança.
    Agora o modelo planeja (vendo a arte), o servidor escolhe a ferramenta e o resultado é conferido."""
    from tests.conftest import RENDER_SECRET, FlaskSession, UserClient, make_app
    from tests.test_workers import FakeOllama, FlaskSite
    from studio.worker_client import WorkerClient
    from workers import hostgator_interpreter

    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 44)
    worker = WorkerClient("http://localhost", "acer", RENDER_SECRET, session=FlaskSession(app))
    site = FlaskSite(app)
    conv = new_conversation(user)

    def say(text, answer):
        user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": text, "anexos": [], "opcoes": {}})
        job = site.call("POST", "/claim", {})["job"]
        assert job["payload"].get("mode") == "creative"
        hostgator_interpreter.process(site, FakeOllama(json.dumps(answer)), job, vision=False)
        return job, user.get(f"/studio/api/conversations/{conv}").get_json()

    def payload(job_id):
        with app.app_context():
            return json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job_id,))["payload"])

    say("cria uma logo pra minha empresa Site Genius Studio", {
        "acao": "gerar", "tipo_pedido": "nova", "resposta": "Vou criar!", "estilo": "logo", "proporcao": "1:1",
        "prompt_visual": "Minimalist abstract logo symbol, blue geometric shapes, white background",
        "textos": [{"texto": "Site Genius Studio", "papel": "titulo", "posicao": "auto"}]})
    assert "plain background" not in payload(user.get(f"/studio/api/conversations/{conv}").get_json()["jobs"][0]["id"])["prompt"]
    render_all(worker, tmp_path)
    ev = site.call("POST", "/claim", {})["job"]  # conferência da criação
    hostgator_interpreter.process(site, FakeOllama(json.dumps({"atendeu": True, "nota": 8, "resumo": "logo criada"})), ev,
                                  vision=True)

    # O modelo vê a arte atual (primeira imagem) e planeja: troca de fundo de verdade + texto e fonte novos.
    plan = {"entendimento": "Fundo tecnológico moderno, fonte de TI e só o nome Site Genius",
            "preservar": ["símbolo azul"], "mudar": [{"elemento": "fundo", "como": "circuitos escuros"},
                                                    {"elemento": "tipografia", "como": "fonte tecnológica"}],
            "estrategia": "reestilizar", "textos_finais": [{"texto": "Site Genius", "papel": "titulo"}],
            "criterios": ["fundo escuro com circuitos", "texto é Site Genius", "fonte diferente"]}
    job, data = say("melhore o fundo, não gostei, deixa mais tecnológico e muda a fonte, pode deixar só Site Genius", {
        "acao": "gerar", "tipo_pedido": "ajuste", "resposta": "Ajustando!", "estilo": "logo", "plano": plan,
        "prompt_visual": "Blue geometric logo symbol on a dark futuristic circuit board background with glowing lines"})
    assert job["payload"]["current_image_id"]  # a arte atual vai para o modelo de visão
    reply = data["mensagens"][-1]["texto"]
    assert "confiro" in reply and "Ajustando!" not in reply  # diz o que vai fazer, não que já fez
    render = data["jobs"][0]
    p = payload(render["id"])
    assert p["render_mode"] == "img2img" and p["denoise"] >= 0.85  # mudança grande = liberdade grande
    assert textplan.plan_texts(p["spec"]["text_plan"]) == ["Site Genius"]
    assert p["spec"]["text_plan"]["estilo"] != "moderno"  # a fonte muda de verdade
    render_all(worker, tmp_path)
    ev = site.call("POST", "/claim", {})["job"]
    assert ev["payload"]["before_id"] and ev["payload"]["after_id"] and ev["payload"]["criterios"]
    hostgator_interpreter.process(site, FakeOllama(json.dumps({"atendeu": False, "nota": 3, "faltou": ["fundo ainda branco"]})),
                                  ev, vision=True)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    retry = data["jobs"][0]
    p2 = payload(retry["id"])
    assert retry["id"] != render["id"] and p2["render_mode"] == "txt2img"  # escalou para recriar
    assert p2["spec"]["pedido_ajuste"]["tentativa"] == 2
    render_all(worker, tmp_path)
    ev = site.call("POST", "/claim", {})["job"]
    hostgator_interpreter.process(site, FakeOllama(json.dumps({"atendeu": False, "nota": 4, "faltou": ["fundo ainda claro"],
                                                               "resumo": "O fundo continua claro."})), ev, vision=True)
    final = user.get(f"/studio/api/conversations/{conv}").get_json()["mensagens"][-1]["texto"]
    assert "sincero" in final and "fundo ainda claro" in final  # na segunda reprovação, honestidade (sem loop)


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



# ------------------------------------------------------------ substituição de textos (defeito "Jefferson", 02/11/2026)


OLD_PLAN = {"versao": 1, "blocos": [{"texto": "tecnologia é aqui", "papel": "titulo", "posicao": "centro"}],
            "estilo": "futurista", "efeito": "brilho", "cor": None, "superficie": None, "destaque": None, "pendentes": []}
VERY_OLD_PLAN = {"blocos": [{"texto": "tecnologia é aqui", "papel": "titulo", "posicao": "centro"}]}  # sem campos novos


def test_jefferson_real_model_answer_keeps_client_spelling():
    """Dados reais: o modelo trocou a caixa ("Jefferson Tecnologia") e a validação descartava a troca."""
    real = {"acao": "ajustar", "tipo_pedido": "ajuste", "ajuste": {"cena": True, "textos": True, "intensidade": "grande",
            "operacoes_texto": [{"acao": "trocar", "alvo": "tecnologia é aqui", "papel": "titulo",
                                 "texto": "Jefferson Tecnologia"}]}}
    msg = "só muda o texto pra Jefferson tecnologia  e troca o fundo por algo melhor mais realista"
    for plan in (OLD_PLAN, VERY_OLD_PLAN):
        d = edits.plan_adjustment({"text_plan": plan}, msg, real, [])
        assert textplan.plan_texts(d["plan"]) == ["Jefferson Tecnologia"] and not d["faltando"]  # escrita melhorada
        assert d["cena"] == "total"
        acao, reply, spec = creative.adjustment_outcome(d, {"text_plan": plan, "prompt_visual": "x"}, "art1",
                                                         "abstract tech", real, {}, msg, [])
        assert acao == "gerar" and spec["render_mode"] == "txt2img" and "Não consegui" not in reply
        assert textplan.plan_texts(spec["text_plan"]) == ["Jefferson Tecnologia"]


def test_safe_writing_polish_never_invents():
    polish = textplan.polish_from_model
    assert polish("Jefferson Tecnologia", "muda pra Jefferson tecnologia")[0] == "Jefferson Tecnologia"
    assert polish("Tecnologia", "escreve tecnolgia no topo")[0] == "Tecnologia"  # erro de digitação
    assert polish("Café Aroma", "placa com cafe aroma")[0] == "Café Aroma"  # acento
    assert polish("Seja Bem-vindo", "frase seja bem vindo")[0] == "Seja Bem-vindo"
    assert polish("Emano Tech", "muda pra Emano Tec") is None  # nome/marca não é "corrigido"
    assert polish("Emanotec", "muda pra EmanoTec")[0] == "EmanoTec"  # grafia estilizada respeitada
    assert polish("Pizzaria Bella", "logo da pizzaria bela") is None  # troca de palavra
    assert polish("R$ 59,90", "preço 49,90") is None  # números nunca mudam
    assert polish("Drogaria Popular Ltda", "muda o nome para drogaria popular") is None  # nada acrescentado
    assert polish("Promoção de Verão!", 'arte com "promoção de verão"')[0] == "promoção de verão"  # aspas: exato


def test_find_literal_uses_client_spelling_only():
    assert textplan.find_literal("Jefferson Tecnologia", "muda pra Jefferson tecnologia agora") == "Jefferson tecnologia"
    assert textplan.find_literal("Café Aroma", "quero cafe aroma na placa") == "cafe aroma"
    assert textplan.find_literal("Loja Nova", "quero outra coisa") is None


MULTI = {"text_plan": {"versao": 1, "estilo": "moderno", "efeito": "auto", "cor": None, "blocos": [
    {"texto": "Farmácia Bem", "papel": "titulo", "posicao": "topo"},
    {"texto": "Entrega grátis", "papel": "subtitulo", "posicao": "rodape"},
    {"texto": "R$ 19,90", "papel": "preco", "posicao": "rodape"}]}}


def test_replacements_without_repeating_previous_text():
    cases = {"muda o nome para Drogaria Popular": ["Drogaria Popular", "Entrega grátis", "R$ 19,90"],
             "troca o preço por 24,90": ["Farmácia Bem", "Entrega grátis", "R$ 24,90"],
             "só altera o título para Promoção Especial": ["Promoção Especial", "Entrega grátis", "R$ 19,90"],
             "muda a frase para Entrega em 1 hora": ["Farmácia Bem", "Entrega em 1 hora", "R$ 19,90"]}
    for msg, expected in cases.items():
        d = edits.plan_adjustment(MULTI, msg, {}, [])
        assert textplan.plan_texts(d["plan"]) == expected, msg
        assert d["cena"] is None, msg  # só texto: mesma arte


def test_ambiguous_replacement_asks_instead_of_guessing():
    d = edits.plan_adjustment(MULTI, "muda o texto pra Promoção Especial", {}, [])
    assert textplan.plan_texts(d["plan"]) == textplan.plan_texts(MULTI["text_plan"]) and d["ambiguo"]
    acao, reply, _ = creative.adjustment_outcome(d, MULTI, "art1", "", {}, {}, "muda o texto pra Promoção Especial", [])
    assert acao == "perguntar" and "Qual texto" in reply and "Farmácia Bem" in reply


def test_add_versus_replace():
    model = {"ajuste": {"cena": False, "textos": True, "operacoes_texto": [
        {"acao": "adicionar", "texto": "Aberto 24h", "papel": "subtitulo"}]}}
    d = edits.plan_adjustment(MULTI, "adiciona também Aberto 24h", model, [])
    assert textplan.plan_texts(d["plan"]) == ["Farmácia Bem", "Entrega grátis", "R$ 19,90", "Aberto 24h"]


def test_single_block_replacement_without_naming_old_text(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, 'arte com "tecnologia é aqui" no centro')
    render_all(render_worker, tmp_path)
    data = send(user, conv, "agora troca o texto para Emano Tec")
    job = data["jobs"][0]
    assert job["tipo"] == "text_edit" and render_worker.claim() is None  # só texto: sem gerar de novo
    with user.client.application.app_context():
        plan = json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"])
    assert textplan.plan_texts(plan["spec"]["text_plan"]) == ["Emano Tec"]
    reply = data["mensagens"][-1]["texto"]
    assert "“tecnologia é aqui” → “Emano Tec”" in reply and "Não consegui" not in reply


def test_reply_never_contradicts_itself(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, 'arte com "tecnologia é aqui" no centro')
    render_all(render_worker, tmp_path)
    send(user, conv, "muda o texto pra Jefferson tecnologia e troca o fundo por algo mais realista")
    render_all(render_worker, tmp_path)
    text = user.get(f"/studio/api/conversations/{conv}").get_json()["mensagens"][-1]["texto"]
    assert "Jefferson Tecnologia" in text and "Não consegui" not in text  # acabamento profissional
    assert not ("Atenção" in text and "os textos estão como você pediu" in text)


# ------------------------------------------------------------ anexos: a imagem enviada tem prioridade (02/11/2026)

MSG_ALUGUEL = ("agora cria uma arte legal para eu divulgar no instagram aproveitando essa arte que ja desolvi em anexo "
               "porém altera o valor do aluguel da imagem para 1000 reais")


def test_reference_changes_from_real_message():
    assert creative.reference_changes(MSG_ALUGUEL) == [{"tipo": "preco", "novo": "R$ 1.000"}]
    assert creative.reference_changes("troca o preço dessa imagem para 1.299,90") == [{"tipo": "preco", "novo": "R$ 1.299,90"}]
    assert creative.reference_changes("muda o whatsapp da arte para (11) 98888-7777") == [
        {"tipo": "telefone", "novo": "(11) 98888-7777"}]
    assert creative.reference_changes("deixa o texto maior") == []


def _jefferson_then_attachment(user, render_worker, tmp_path, refedit_online):
    from tests.test_studio_api import upload
    from tests.conftest import image_bytes
    conv = new_conversation(user)
    send(user, conv, 'arte tecnológica com "Jefferson Tecnologia" no centro')
    render_all(render_worker, tmp_path)
    if refedit_online:
        render_worker.claim(["reference_edit"])  # agente v2.5 anuncia a capacidade (fila vazia aqui)
    ref = upload(user, kind="reference", data=image_bytes("JPEG", (560, 700))).get_json()["asset"]["id"]
    data = send(user, conv, MSG_ALUGUEL, anexos=[ref])
    return conv, ref, data


def test_attachment_is_used_not_previous_art(user, render_worker, tmp_path):
    conv, ref, data = _jefferson_then_attachment(user, render_worker, tmp_path, refedit_online=True)
    job = data["jobs"][0]
    assert job["tipo"] == "reference_edit"
    with user.client.application.app_context():
        payload = json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"])
    assert payload["asset_ids"] == [ref] and payload["trocas"] == [{"tipo": "preco", "novo": "R$ 1.000"}]
    assert "Jefferson" not in json.dumps(payload, ensure_ascii=False)  # nada herdado do projeto anterior
    reply = data["mensagens"][-1]["texto"]
    assert "imagem que você enviou" in reply and "R$ 1.000" in reply and "Instagram" in reply
    # Worker antigo (sem a capacidade) não pega essa tarefa; o v2.5 pega.
    assert render_worker.claim() is None
    claimed = render_worker.claim(["reference_edit"])
    assert claimed and claimed["payload"]["gen_type"] == "reference_edit"


def test_attachment_without_capable_worker_is_honest(user, render_worker, tmp_path):
    conv, ref, data = _jefferson_then_attachment(user, render_worker, tmp_path, refedit_online=False)
    assert not [j for j in data["jobs"] if j["tipo"] == "reference_edit"]
    reply = data["mensagens"][-1]["texto"]
    assert "não consigo alterar textos diretamente" in reply and "Jefferson" not in reply


def test_attachment_never_inherits_previous_art_in_model_or_rules():
    prev = {"prompt_visual": "tech", "text_plan": {"versao": 1, "blocos": [
        {"texto": "Jefferson Tecnologia", "papel": "titulo", "posicao": "centro"}], "estilo": "futurista"}}
    model = {"acao": "gerar", "tipo_pedido": "ajuste", "editar_anterior": True, "prompt_visual": "real estate ad",
             "ajuste": {"cena": True, "textos": True}, "uso_referencia": "base"}
    acao, _, spec = creative.normalize_model_answer(model, "cria uma arte para instagram com essa foto", {}, prev,
                                                    "old_art", ["ref1"], [])
    assert spec["init_asset_id"] == "ref1" and "Jefferson" not in json.dumps(spec, ensure_ascii=False)
    acao, _, spec = creative.rules_interpretation("agora cria uma arte com essa foto", {}, prev, "old_art", ["ref1"],
                                                  None, [])
    assert spec["init_asset_id"] == "ref1" and "Jefferson" not in json.dumps(spec, ensure_ascii=False)


def test_agent_reference_edit_without_ocr_fails_honestly(user, render_worker, tmp_path, monkeypatch):
    from workers import acer_agent
    conv, ref, data = _jefferson_then_attachment(user, render_worker, tmp_path, refedit_online=True)
    job = render_worker.claim(["reference_edit"])
    from studio import refedit
    monkeypatch.setattr(refedit.ocr, "engine", lambda: None)
    acer_agent.process(render_worker, job, None, acer_agent.LocalTimings(str(tmp_path / "t.json")))
    text = user.get(f"/studio/api/conversations/{conv}").get_json()["mensagens"][-1]["texto"]
    assert "Não consegui fazer essa troca" in text


def test_refedit_real_ocr_when_available():
    import pytest
    pytest.importorskip("rapidocr_onnxruntime")
    pytest.importorskip("cv2")
    from studio import refedit
    img = Image.new("RGB", (800, 1000), (245, 241, 232))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((60, 700, 560, 840), 20, fill=(232, 120, 20))
    draw.text((90, 800), "R$ 1.500", font=typography.get_font("poppins", 800, 70), fill=(255, 255, 255), anchor="ls")
    out, report = refedit.edit(png(img), [{"tipo": "preco", "novo": "R$ 1.000"}])
    assert report["ok"] and out


def test_dedicated_runner_only_gets_reference_edits(user, render_worker, tmp_path):
    conv = new_conversation(user)
    send(user, conv, "um farol ao entardecer")  # tarefa comum na fila
    assert render_worker.claim(["reference_edit"], only=["reference_edit"]) is None
    assert render_worker.claim()["payload"]["gen_type"] == "free_image"


# ------------------------------------------------------------ redesign comercial, briefing e busca de fotos


def test_commercial_redesign_never_regenerates_products(user):
    from tests.test_studio_api import upload
    conv = new_conversation(user)
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    for msg in ["Consegue melhorar essa imagem deixando ela mais moderna e melhor distribuída? Em um layout mais atual.",
                "Consegue melhorar essa imagem deixando ela mais atual e com um layout mais moderno mantendo as "
                "informações de preço e produto e tudo mais que tem na foto?"]:
        data = send(user, conv, msg, anexos=[ref])
        assert not [j for j in data["jobs"] if j["tipo"] == "free_image"], msg  # nenhuma geração img2img
        assert "não redesenho os produtos" in data["mensagens"][-1]["texto"]
    assert data["conversa"]["modo"] == "ad"


def test_prices_without_cents_are_grounded_and_ean_not_invented():
    from studio import briefing as bf
    msg = ("Cria uma promoção pra mim de produtos de ofertas pode ser tipo um jornal de ofertas leite de promoção "
           "comprada por quantidade levando 2 paga 45 reais e 1 só é 55 e tbm promoção só no preço no caso da toalha "
           "umedecida sai de 14,99 para 9,99 e tbm o lenço de 15 para 10 reais pode usar sua criatividade")
    model = {"loja": "Drogarias Poupaqui", "formato": "story", "produtos": [
        {"nome": "Leite", "ean": "7891000065440", "preco": "55,00"},
        {"nome": "Toalha umedecida", "preco": "9,99", "preco_de": "14,99"},
        {"nome": "Lenço", "preco": "10,00", "preco_de": "15,00"}]}
    out = bf.normalize_briefing(model, [msg])
    prices = {p["nome"]: (p["preco"], p["preco_de"], p["ean"]) for p in out["produtos"]}
    assert prices == {"Leite": ("R$ 55,00", "", ""), "Toalha umedecida": ("R$ 9,99", "R$ 14,99", ""),
                      "Lenço": ("R$ 10,00", "R$ 15,00", "")}
    assert not [p for p in out["pendencias"] if "preço" in p]  # não pede de novo o que foi informado


def test_internet_photo_search_validates_match(monkeypatch):
    from studio import products
    leite = {"nome": "Leite Integral Italac 1L", "marca": "Italac", "image_url": "https://images.openfoodfacts.org/x.jpg",
             "license": "CC BY-SA 3.0", "attribution": "Open Food Facts contributors"}
    celular = {**leite, "nome": "Smartphone X", "marca": "Y"}
    moca = {**leite, "nome": "Leite moça", "marca": "Nestlé"}
    monkeypatch.setattr(products, "search_name", lambda q, http=None, limit=12: [celular, moca, leite])
    chosen, options = products.find_photo({"nome": "Leite"})  # genérico: não escolhe sozinho; "Leite moça" é condensado
    assert chosen is None and [o["nome"] for o in options] == ["Leite Integral Italac 1L"]
    chosen, _ = products.find_photo({"nome": "Leite Integral Italac"})
    assert chosen["nome"] == "Leite Integral Italac 1L"
    monkeypatch.setattr(products, "search_name", lambda q, http=None, limit=12: [celular])
    assert products.find_photo({"nome": "Toalha umedecida"}) == (None, [])  # nada que corresponda: não usa
    assert products.wants_internet_photos("Pega da internet a foto")


def test_photo_search_needs_brand_and_same_product_type(monkeypatch):
    from studio import products
    base = {"image_url": "https://images.openfoodfacts.org/x.jpg", "license": "CC BY-SA 3.0", "attribution": "OFF"}
    creme = {**base, "nome": "Creme de Leite Italac", "marca": "Italac"}
    tirol = {**base, "nome": "Leite integral Tirol", "marca": "Tirol"}
    italac = {**base, "nome": "Leite UHT Integral Italac", "marca": "Italac"}
    monkeypatch.setattr(products, "search_name", lambda q, http=None, limit=12: [creme, tirol, italac])
    chosen, options = products.find_photo({"nome": "Leite integral"})  # sem marca: o cliente escolhe
    assert chosen is None and [o["nome"] for o in options] == ["Leite integral Tirol", "Leite UHT Integral Italac"]
    chosen, _ = products.find_photo({"nome": "Leite Italac"})  # marca: escolhe, mas nunca "Creme de leite"
    assert chosen["nome"] == "Leite UHT Integral Italac"
    assert products._candidate("OFF", {"product_name": "X", "image_url": "https://images.openfoodfacts.org/v.jpg"}) is None


def test_redesign_extract_fills_panel_with_real_data(user, render_worker, tmp_path):
    """Redesign: o Acer lê a arte e o painel recebe os produtos/preços/fotos originais (nada gerado por IA)."""
    import base64
    from tests.test_studio_api import upload
    from tests.conftest import image_bytes
    conv = new_conversation(user)
    render_worker.claim(["reference_edit"])  # agente v2.5 online
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    data = send(user, conv, "Consegue melhorar essa imagem deixando ela mais atual mantendo as informações de preço e produto",
                anexos=[ref])
    job = render_worker.claim(["reference_edit"])
    assert job["payload"]["gen_type"] == "reference_read" and job["payload"]["asset_ids"] == [ref]
    crop = image_bytes("JPEG", (120, 180))
    render_worker.extracted(job["id"], {"titulo": "Ofertas da Semana", "loja": "Mercado Bom Preço", "produtos": [
        {"nome": "Leite Integral Italac 1L", "preco": "4,99", "preco_de": "", "conferir": []},
        {"nome": "Arroz Tio João 5kg", "preco": "27,90", "preco_de": "31,00", "conferir": ["preco_de"]}]},
        [crop, crop], {"ok": True})
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    produtos = data["briefing"]["produtos"]
    assert [(p["nome"], p["preco"], p["preco_de"], bool(p["asset_id"])) for p in produtos] == [
        ("Leite Integral Italac 1L", "R$ 4,99", "", True), ("Arroz Tio João 5kg", "R$ 27,90", "", True)]  # incerto: vazio
    assert data["conversa"]["modo"] == "ad" and data["briefing"]["loja"] == "Mercado Bom Preço"
    text = data["mensagens"][-1]["texto"]
    assert "Li a sua arte" in text and "Arroz Tio João 5kg: preço antigo (riscado) — li “R$ 31,00”, confirme o valor" in text
    assert not [j for j in data["jobs"] if j["tipo"] == "free_image"]


def test_job_type_names_fit_database_column():
    """gen_type é VARCHAR(16) no Postgres (o SQLite dos testes não reclama)."""
    import re as _re
    from pathlib import Path
    src = Path("studio/routes.py").read_text(encoding="utf-8")
    names = set(_re.findall(r"'(free_image|text_edit|reference_\w+|ad_composite)'", src))
    assert names and all(len(n) <= 16 for n in names), names


# ------------------------------------------------------------ encarte só pela conversa (designer conversacional)


def _flyer(user, conv, text):
    return send(user, conv, text)


def test_flyer_entirely_by_chat_with_styles_choice_and_generation(user):
    from tests.test_studio_api import upload
    conv = new_conversation(user)
    data = _flyer(user, conv, "Cria um encarte para a Drogarias PoupAqui.")
    assert data["conversa"]["modo"] == "ad" and data["briefing"]["loja"] == "Drogarias PoupAqui"
    data = _flyer(user, conv, "Coloca leite integral por R$ 4,99, arroz por R$ 31,90 e café Pilão por R$ 18,99.")
    assert [(p["nome"], p["preco"]) for p in data["briefing"]["produtos"]] == [
        ("Leite integral", "R$ 4,99"), ("Arroz", "R$ 31,90"), ("Café Pilão", "R$ 18,99")]
    # Fotos: o cliente envia pelo chat (a busca externa é testada à parte).
    for nome in ("leite", "arroz", "café"):
        pid = upload(user, kind="product").get_json()["asset"]["id"]
        b = user.get(f"/studio/api/conversations/{conv}").get_json()["briefing"]
        for p in b["produtos"]:
            if p["nome"].lower().startswith(nome):
                p["asset_id"] = pid
        user.put(f"/studio/api/conversations/{conv}/briefing", json={"briefing": b})  # painel: mesma fonte
    data = _flyer(user, conv, "Quero formato Story.")
    assert data["briefing"]["formato"] == "story"
    data = _flyer(user, conv, "Me mostra alguns estilos.")
    previews = data["mensagens"][-1]["anexos"]
    # Prévias = composições livres (variações de verdade, não só modelos fixos).
    assert len(previews) == 3 and data["briefing"]["layout"]["previas"] == ["criativo:1", "criativo:2", "criativo:3"]
    data = _flyer(user, conv, "Gostei do segundo, mas quero o café maior e o fundo vermelho.")
    lay = data["briefing"]["layout"]
    assert lay["modelo"] == "criativo" and lay["semente"] == 2 and lay["destaque"] == "Café Pilão"
    assert lay["cores"] == [[215, 25, 32]]
    data = _flyer(user, conv, "Pode gerar.")
    job = next(j for j in data["jobs"] if j["tipo"] == "flyer")
    assert job["status"] == "done" and job["resultado_asset_id"]
    with user.client.application.app_context():
        payload = json.loads(get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"])
    # A arte usa exatamente os dados do briefing.
    assert [(p["nome"], p["preco"]) for p in payload["briefing"]["produtos"]] == [
        ("Leite integral", "R$ 4,99"), ("Arroz", "R$ 31,90"), ("Café Pilão", "R$ 18,99")]
    img = Image.open(io.BytesIO(user.get(f"/studio/api/assets/{job['resultado_asset_id']}").data))
    assert img.size == (1080, 1920)
    # Edição depois de gerado, pelo chat; o painel vê o mesmo valor.
    data = _flyer(user, conv, "Troca o preço do arroz para R$ 29,90.")
    assert next(p for p in data["briefing"]["produtos"] if p["nome"] == "Arroz")["preco"] == "R$ 29,90"
    data = _flyer(user, conv, "Remove o leite.")
    assert [p["nome"] for p in data["briefing"]["produtos"]] == ["Arroz", "Café Pilão"]
    data = _flyer(user, conv, "Coloca o café primeiro")
    assert [p["nome"] for p in data["briefing"]["produtos"]] == ["Café Pilão", "Arroz"]


def test_uncertain_price_confirmed_by_chat_and_panel_sync(user):
    conv = new_conversation(user)
    _flyer(user, conv, "Cria um encarte para a Loja Teste.")
    _flyer(user, conv, "Coloca arroz por 27,90")
    b = user.get(f"/studio/api/conversations/{conv}").get_json()["briefing"]
    b["produtos"][0]["preco_de"] = ""
    b["confirmar"] = [{"produto": "Arroz", "campo": "preco_de", "lido": "R$ 31,00"}]
    with user.client.application.app_context():
        get_conn().run("UPDATE sg_conversations SET briefing = ? WHERE id = ?", (json.dumps(b), conv))
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    assert not data["pode_gerar"]  # preço incerto bloqueia a geração
    data = _flyer(user, conv, "31,90")
    produto = data["briefing"]["produtos"][0]
    assert produto["preco_de"] == "R$ 31,90" and data["briefing"]["confirmar"] == []
    # Painel altera o preço; a conversa enxerga o novo valor.
    b = data["briefing"]
    b["produtos"][0]["preco"] = "R$ 26,90"
    user.put(f"/studio/api/conversations/{conv}/briefing", json={"briefing": b})
    data = _flyer(user, conv, "Quero formato quadrado")
    assert data["briefing"]["produtos"][0]["preco"] == "R$ 26,90" and data["briefing"]["formato"] == "quadrado"


def test_context_switch_keeps_flyer_and_free_art_separate(user, render_worker, tmp_path):
    conv = new_conversation(user)
    _flyer(user, conv, "Cria um encarte para a Drogarias PoupAqui.")
    _flyer(user, conv, "Coloca leite integral por R$ 4,99")
    data = send(user, conv, "agora cria uma arte de um astronauta na lua")
    assert data["conversa"]["modo"] == "free"
    job = next(j for j in data["jobs"] if j["tipo"] == "free_image")
    with user.client.application.app_context():
        payload = get_conn().one("SELECT payload FROM sg_jobs WHERE id = ?", (job["id"],))["payload"]
    assert "Leite" not in payload and "4,99" not in payload  # nada do encarte na arte nova
    data = send(user, conv, "volta pro encarte")
    assert data["conversa"]["modo"] == "ad" and data["briefing"]["produtos"][0]["nome"] == "Leite integral"


def test_no_control_characters_in_source():
    """Um '\b' escrito errado vira backspace e quebra regex sem erro visível (já aconteceu)."""
    from pathlib import Path
    bad = []
    for path in list(Path("studio").glob("*.py")) + list(Path("workers").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            bad.append(str(path))
    assert bad == []
