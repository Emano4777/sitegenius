"""v2.6: catálogo de fotos, escolha pela conversa, vários encartes, referência visual e geração executada pelo chat."""
import json

from studio.db import get_conn
from tests.conftest import UserClient, image_bytes, make_app
from tests.test_creative import send
from tests.test_studio_api import new_conversation, upload
from tests.test_workers import FakeOllama, FlaskSite
from workers import hostgator_interpreter

OFF = {"license": "CC BY-SA 3.0", "attribution": "Open Food Facts contributors", "fonte": "Open Food Facts",
       "pagina": "https://world.openfoodfacts.org/product/1"}


def _fake_off(monkeypatch, options):
    from studio import routes
    monkeypatch.setattr(routes, "find_photo", lambda product, http=None: (None, options))
    monkeypatch.setattr(routes, "safe_download", lambda url, limit: image_bytes("JPEG", (400, 400), (230, 60, 40)))


def test_photo_options_choice_and_catalog_reuse(user, monkeypatch):
    opts = [{**OFF, "nome": "Leite integral Tirol", "marca": "Tirol", "quantidade": "1 L", "ean": "7896256600223",
             "image_url": "https://images.openfoodfacts.org/a.jpg"},
            {**OFF, "nome": "Leite Integral Piracanjuba", "marca": "Piracanjuba", "quantidade": "1 L", "ean": "7898215151708",
             "image_url": "https://images.openfoodfacts.org/b.jpg"}]
    _fake_off(monkeypatch, opts)
    conv = new_conversation(user)
    send(user, conv, "Cria um encarte para o Mercado Bom Preço.")
    send(user, conv, "Coloca leite integral por R$ 4,99.")
    data = send(user, conv, "Busca as fotos na internet.")
    b = data["briefing"]
    assert not b["produtos"][0]["asset_id"]  # sem marca/apresentação: não escolhe sozinho
    assert [o["nome"] for o in b["opcoes_foto"]["Leite integral"]] == ["Leite integral Tirol 1 L",
                                                                      "Leite Integral Piracanjuba 1 L"]
    msg = data["mensagens"][-1]
    assert "usa a 2 do leite" in msg["texto"] and msg["anexos"]  # folha numerada com as opções
    data = send(user, conv, "usa a 2 do leite")
    b = data["briefing"]
    assert b["produtos"][0]["asset_id"]
    assert b["opcoes_foto"] == {} and "Coloquei a foto 2" in data["mensagens"][-1]["texto"]
    with user.client.application.app_context():
        row = get_conn().one("SELECT * FROM sg_catalog WHERE nome = 'Leite integral'")
    assert row["nome"] == "Leite integral" and "licença aberta" in row["autorizacao"] and row["apresentacao"] == "1 L"
    # Outro encarte do mesmo cliente: o catálogo vem antes de qualquer busca.
    monkeypatch.setattr("studio.routes.find_photo", lambda product, http=None: (_ for _ in ()).throw(AssertionError("não buscar")))
    conv2 = new_conversation(user)
    send(user, conv2, "Cria um encarte para o Mercado Bom Preço.")
    send(user, conv2, "Coloca leite integral por R$ 5,49.")
    data = send(user, conv2, "Busca as fotos.")
    assert data["briefing"]["produtos"][0]["asset_id"] and "do seu catálogo" in data["mensagens"][-1]["texto"]


def test_several_flyers_in_one_conversation(user):
    conv = new_conversation(user)
    send(user, conv, "Cria um encarte para o Mercado Bom Preço.")
    send(user, conv, "Coloca arroz por R$ 24,90 e feijão por R$ 8,49.")
    data = send(user, conv, "Agora faz um novo encarte para a Farmácia Vida com dipirona por 9,90")
    b = data["briefing"]
    assert b["loja"] == "Farmácia Vida" and [p["nome"] for p in b["produtos"]] == ["Dipirona"]
    assert "anterior ficou guardado" in data["mensagens"][-1]["texto"]
    data = send(user, conv, "Volta pro encarte do mercado")
    b = data["briefing"]
    assert b["loja"] == "Mercado Bom Preço" and [p["nome"] for p in b["produtos"]] == ["Arroz", "Feijão"]
    data = send(user, conv, "Volta pro encarte da farmácia")
    assert data["briefing"]["loja"] == "Farmácia Vida"
    data = send(user, conv, "quais encartes tenho?")
    assert "Mercado Bom Preço" in data["mensagens"][-1]["texto"]


def test_reference_analysis_then_automatic_generation(tmp_path):
    """Referência anexada -> análise (ferramentas) -> “gere então” espera a análise e gera sozinho, sem clicar."""
    app = make_app(tmp_path)
    user = UserClient(app, 61)
    site = FlaskSite(app)
    site.call("POST", "/claim", {"capacidades": ["tools"], "somente": ["tools"]})  # ferramentas online
    conv = new_conversation(user)
    send(user, conv, "Cria um encarte para a Drogarias PoupAqui.")
    send(user, conv, "Coloca toalha umedecida de 14,99 por 9,99 e lenço de 15 por 10 reais.")
    ref = upload(user, kind="reference").get_json()["asset"]["id"]
    data = send(user, conv, "algo mais dinâmico tipo a imagem em anexo", anexos=[ref])
    assert data["briefing"]["layout"]["referencia"]["estado"] == "analisando"
    data = send(user, conv, "gere então")
    assert "gero o encarte automaticamente" in data["mensagens"][-1]["texto"]
    assert not [j for j in data["jobs"] if j["tipo"] == "flyer"]  # não afirma nem gera antes da análise
    job = site.call("POST", "/claim", {"capacidades": ["tools"], "somente": ["tools"]})["job"]
    assert job["payload"]["mode"] == "layout_ref" and job["payload"]["asset_id"] == ref
    assert site.call("GET", f"/assets/{ref}", raw=True)[:2] == b"\xff\xd8"
    spec = {"versao": 2, "fundo_conteudo": [255, 255, 255], "cabecalho": {"altura": 0.25, "principal": [199, 32, 28],
            "cores": [[199, 32, 28], [240, 240, 240]], "selo": [231, 200, 66]},
            "tarja": {"inicio": 0.25, "fim": 0.3, "cor": [199, 32, 28], "cor_texto": [255, 255, 255]},
            "produtos": {"linhas": 3, "total": 12, "por_linha": [4, 4, 2], "recortados": True},
            "preco": {"cor": [12, 12, 12], "centavos_sobrescritos": True, "posicao": "direita", "selo_fracao": 0.4,
                      "selo_cor": [232, 205, 161], "selo_forma": "explosao", "altura_rel": 0.48},
            "paleta": [[253, 252, 251], [196, 53, 44]], "visual": {"organizacao": "tabloide", "descricao": "x" * 300}}
    site.call("POST", f"/jobs/{job['id']}/complete", {"layout_ref": spec, "relatorio": {"ok": True}})
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    lay = data["briefing"]["layout"]
    assert lay["modelo"] == "referencia" and lay["referencia"]["estado"] == "pronta"
    flyer = [j for j in data["jobs"] if j["tipo"] == "flyer"]
    assert len(flyer) == 1 and flyer[0]["status"] == "done"  # gerou sozinho, uma vez
    text = next(m["texto"] for m in data["mensagens"] if m["texto"].startswith("Analisei a sua referência"))
    assert "cabeçalho colorido" in text and "Pronto! Gerei o encarte" in text
    data = send(user, conv, "pode gerar")  # nada mudou: não duplica
    assert len([j for j in data["jobs"] if j["tipo"] == "flyer"]) == 1 and "já está pronto" in data["mensagens"][-1]["texto"]


def test_model_decides_generation_and_never_fakes_it(tmp_path):
    app = make_app(tmp_path, interpreter_mode="queue")
    user = UserClient(app, 62)
    site = FlaskSite(app)
    conv = new_conversation(user)
    send(user, conv, "Cria um encarte para a Drogarias PoupAqui.")
    send(user, conv, "Coloca dipirona por R$ 9,90.")
    pid = upload(user, kind="product").get_json()["asset"]["id"]
    b = user.get(f"/studio/api/conversations/{conv}").get_json()["briefing"]
    b["produtos"][0]["asset_id"] = pid
    user.put(f"/studio/api/conversations/{conv}/briefing", json={"briefing": b})
    user.post(f"/studio/api/conversations/{conv}/messages", json={"texto": "tá ótimo, bora ver como fica na arte final então"})
    job = site.call("POST", "/claim", {})["job"]
    answer = {"resposta": "Gerando o encarte com o layout dinâmico!", "gerar": True,
              "briefing": {**b, "produtos": [{"nome": "Dipirona", "preco": "9,90"}]}}
    hostgator_interpreter.process(site, FakeOllama(json.dumps(answer)), job, vision=False)
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    flyer = [j for j in data["jobs"] if j["tipo"] == "flyer"]
    assert len(flyer) == 1 and flyer[0]["status"] == "done"
    last = data["mensagens"][-1]
    assert last["job_id"] == flyer[0]["id"] and "Gerando" not in last["texto"] and "Pronto! Gerei" in last["texto"]


def test_two_references_layout_from_one_colors_from_other(tmp_path):
    app = make_app(tmp_path)
    user = UserClient(app, 63)
    site = FlaskSite(app)
    site.call("POST", "/claim", {"capacidades": ["tools"], "somente": ["tools"]})
    conv = new_conversation(user)
    send(user, conv, "Cria um encarte para a Drogarias PoupAqui.")
    send(user, conv, "Coloca lenço de 15 por 10 reais.")
    r1 = upload(user, kind="reference").get_json()["asset"]["id"]
    r2 = upload(user, kind="reference").get_json()["asset"]["id"]
    send(user, conv, "quero o layout da primeira imagem com as cores da segunda", anexos=[r1, r2])
    job = site.call("POST", "/claim", {"capacidades": ["tools"], "somente": ["tools"]})["job"]
    assert job["payload"]["asset_ids"] == [r1, r2]
    red = {"versao": 2, "fundo_conteudo": [255, 255, 255], "cabecalho": {"altura": 0.25, "principal": [200, 30, 30],
           "cores": [[200, 30, 30]], "selo": None}, "produtos": {"linhas": 2, "total": 6, "recortados": True},
           "preco": {"cor": [10, 10, 10], "posicao": "direita", "centavos_sobrescritos": True}, "paleta": [[200, 30, 30]]}
    blue = {"versao": 2, "fundo_conteudo": [230, 240, 255], "cabecalho": {"altura": 0.1, "principal": [0, 90, 200],
            "cores": [[0, 90, 200]], "selo": [0, 200, 120]}, "produtos": {"linhas": 1, "total": 3, "recortados": False},
            "preco": {"cor": [0, 60, 160], "posicao": "abaixo", "selo_cor": [0, 90, 200]}, "paleta": [[0, 90, 200]]}
    site.call("POST", f"/jobs/{job['id']}/complete", {"layout_refs": [red, blue], "relatorio": []})
    data = user.get(f"/studio/api/conversations/{conv}").get_json()
    ref = data["briefing"]["layout"]["referencia"]
    assert ref["papeis"] == {"estrutura": 0, "cores": 1} and len(ref["specs"]) == 2
    assert ref["spec"]["cabecalho"]["altura"] == 0.25 and ref["spec"]["cabecalho"]["principal"] == [0, 90, 200]
    assert ref["spec"]["preco"]["posicao"] == "direita" and ref["spec"]["fundo_conteudo"] == [230, 240, 255]
    assert "organização da 1ª imagem e as cores da 2ª" in next(m["texto"] for m in data["mensagens"]
                                                               if m["texto"].startswith("Analisei as suas 2"))
    data = send(user, conv, "agora usa as cores da primeira imagem")
    ref = data["briefing"]["layout"]["referencia"]
    assert ref["papeis"]["cores"] == 0 and ref["spec"]["cabecalho"]["principal"] == [200, 30, 30]
