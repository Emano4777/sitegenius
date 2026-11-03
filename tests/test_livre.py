"""Composição livre: as regras valem para QUALQUER arte. Cada categoria (supermercado, farmácia, clínica, imobiliária,
tecnologia) é testada com 1 produto, vários e nenhum, com planos bons e com planos ruins/truncados do modelo."""
import io
import json

import pytest
from PIL import Image, ImageDraw

from studio.livre import cena, layout, pipeline, plano, valida

SIZE = (1080, 1350)


def photo(color, w=420, h=600, clean=True):
    """Foto sintética de embalagem: fundo branco liso (recortável) ou fundo bagunçado (foto fraca)."""
    im = Image.new("RGB", (w + 80, h + 80), "white" if clean else (120, 90, 60))
    d = ImageDraw.Draw(im)
    if not clean:
        for i in range(0, w + 80, 23):
            d.line([(i, 0), (0, i)], fill=(30, 140, 60), width=7)
    d.rounded_rectangle([40, 40, 40 + w, 40 + h], 30, fill=color)
    d.rectangle([60, 60 + h // 3, 20 + w, 60 + h // 2], fill="white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


CASES = {
    "supermercado": {
        "loja": "Mercado Bom Preço", "titulo": "Ofertas da Semana", "segmento": "supermercado",
        "itens": [("Café Pilão Tradicional 500g", "R$ 17,90", "R$ 19,90", (190, 20, 20), True),
                  ("Arroz Tio João 5kg", "R$ 27,90", None, (20, 60, 160), True),
                  ("Leite Integral 1L", "R$ 5,49", None, None, False),
                  ("Feijão Carioca 1kg", "R$ 8,99", None, (200, 140, 20), True),
                  ("Açúcar Refinado 1kg", "R$ 4,79", None, (230, 230, 240), True)]},
    "farmacia": {
        "loja": "Drogaria Saúde+", "titulo": "", "segmento": "farmácia",
        "itens": [("Protetor Solar FPS 50 200ml", "R$ 49,90", "R$ 69,90", (250, 140, 20), True),
                  ("Shampoo Anticaspa 200ml", "R$ 18,90", None, (20, 40, 90), "fraca"),
                  ("Creme Dental 90g", "R$ 4,99", None, (220, 30, 30), True),
                  ("Sabonete Glicerina 90g", "R$ 6,49", None, None, False)]},
    "clinica": {
        "loja": "Clínica Sorriso Leve", "titulo": "Agende sua avaliação", "segmento": "clínica odontológica",
        "info": {"whatsapp": "(11) 99999-0000", "endereco": "Rua das Flores, 120"},
        "itens": [("Limpeza e profilaxia", "R$ 120,00", None, None, False),
                  ("Clareamento a laser", "R$ 690,00", "R$ 890,00", None, False),
                  ("Consulta de avaliação", "R$ 80,00", None, None, False)]},
    "imobiliaria": {
        "loja": "Lar Ideal Imóveis", "titulo": "Lar Ideal Imóveis", "segmento": "imobiliária",
        "info": {"creci": "12345-J"},
        "itens": [("Apartamento 2 quartos, 62 m², Vila Nova", "R$ 1.800/mês", None, None, False),
                  ("Casa 3 quartos com quintal", "R$ 420.000", None, None, False),
                  ("Studio mobiliado no centro", "R$ 1.350/mês", None, None, False)]},
    "tecnologia": {
        "loja": "TechPoint", "titulo": "Semana Tech", "segmento": "loja de tecnologia",
        "itens": [("Fone Bluetooth com cancelamento de ruído", "R$ 299,00", "R$ 399,00", (30, 30, 34), True),
                  ("Mouse sem fio ergonômico", "R$ 89,90", None, (90, 90, 100), True),
                  ("Teclado mecânico RGB", "R$ 249,00", None, None, False)]},
}

PLANS = {  # planos que um diretor poderia dar (variados) — a geometria vem toda do resolvedor
    "supermercado": {"heroi": 0, "layout": {"titulo": "topo", "heroi": "centro", "preco": "abaixo_heroi", "demais": "fila_base", "objetos": "direita"},
                     "titulo": {"fonte": "bungee", "ef": "neon", "cor": "#ff3030"}, "chamada": {"txt": "Aproveite", "fonte": "satisfy", "ef": "sombra"},
                     "selo": {"txt": "Oferta especial", "f": "estrela"}, "preco": {"estilo": "faixa", "fonte": "anton", "bg": "#ffd400"},
                     "icones": ["flame", "clock"], "cenario": {"ambiente": "fundo vermelho com luzes", "superficie": "bancada", "objetos": ["xícara de café fumegante"]}},
    "farmacia": {"layout": {"titulo": "topo", "heroi": "esquerda", "preco": "ao_lado_heroi", "demais": "coluna_lateral", "objetos": "nenhum"},
                 "titulo": {"txt": "Cuide de você", "fonte": "poppins", "ef": "sombra", "cor": "#ffffff"},
                 "preco": {"estilo": "bloco", "fonte": "russo", "bg": "#0a7c5a", "fg": "#ffffff"}, "icones": ["heart", "shield-check"],
                 "cenario": {"ambiente": "branco e verde-água, luz suave", "superficie": "prateleira", "objetos": ["folhas verdes", "gotas de água"]}},
    "clinica": {"layout": {"titulo": "topo", "heroi": "centro", "preco": "abaixo_heroi", "demais": "grade", "objetos": "ambos"},
                "titulo": {"fonte": "dmserif", "ef": "sombra", "cor": "#ffffff"}, "chamada": {"txt": "Seu sorriso merece", "fonte": "greatvibes", "ef": "sombra"},
                "preco": {"estilo": "etiqueta", "fonte": "poppins", "bg": "#1d6fd8", "fg": "#ffffff"},
                "cenario": {"ambiente": "consultório claro e moderno, azul suave", "superficie": "nenhuma", "objetos": ["planta em vaso"]}},
    "imobiliaria": {"heroi": 0, "layout": {"titulo": "base", "heroi": "centro", "preco": "abaixo_heroi", "demais": "fila_base", "objetos": "atras"},
                    "titulo": {"fonte": "cinzel", "ef": "metal", "cor": "#d4af37"}, "selo": {"txt": "Pronto para morar", "f": "fita"},
                    "preco": {"estilo": "bloco", "fonte": "bebas", "bg": "#111111", "fg": "#d4af37"},
                    "cenario": {"ambiente": "fachada de casa moderna ao entardecer", "superficie": "nenhuma", "objetos": []}},
    "tecnologia": {"heroi": 0, "layout": {"titulo": "topo", "heroi": "direita", "preco": "sobre_heroi", "demais": "fila_base", "objetos": "esquerda"},
                   "titulo": {"fonte": "orbitron", "ef": "neon", "cor": "#22d3ee"}, "chamada": {"txt": "Tecnologia que conecta", "fonte": "russo", "ef": "gradiente"},
                   "preco": {"estilo": "explosao", "fonte": "russo", "bg": "#22d3ee", "fg": "#0b0b14"}, "icones": ["zap", "rocket", "sparkles"],
                   "cenario": {"ambiente": "azul escuro com linhas de circuito brilhantes", "superficie": "pedestal", "objetos": ["linhas de luz neon"]}},
}
BAD_PLANS = ["", "{}", '{"heroi":99,"layout":{"titulo":"lado","preco":"xyz"},"titulo":{"fonte":"comic"}',  # truncado e inválido
             json.dumps({"chamada": {"txt": "50% OFF garantido"}, "selo": {"txt": "Mercado Bom Preço"},
                         "cenario": {"objetos": ["embalagem do produto", "logo da loja", "letreiro com preço"]}})]


def briefing(cat, count):
    c = CASES[cat]
    itens = c["itens"][:1] if count == "um" else c["itens"] if count == "varios" else []
    b = {"loja": c["loja"], "titulo": c["titulo"], "segmento": c["segmento"], "formato": "feed_4x5", "info": c.get("info"),
         "produtos": [{"nome": n, "preco": p, "preco_de": de, "asset_id": "x" if col else None} for n, p, de, col, _ in itens]}
    photos = {i: photo(col, clean=ok is True) for i, (_, _, _, col, ok) in enumerate(itens) if col}
    return b, photos


def solve_case(cat, count, raw):
    b, photos = briefing(cat, count)
    info = {i: pipeline.photo_info(d) for i, d in photos.items()}
    q = {i: v["qualidade"] for i, v in info.items()}
    plan = plano.normalize(plano.parse(raw if isinstance(raw, str) else json.dumps(raw)), b, q)
    return b, info, plan, layout.solve(plan, b, info, SIZE)


ALL = [(cat, cnt, kind) for cat in CASES for cnt in ("um", "varios", "nenhum") for kind in ("bom", 0, 1, 2, 3)]


@pytest.mark.parametrize("cat,count,kind", ALL)
def test_geometry_invariants_any_art(cat, count, kind):
    raw = PLANS[cat] if kind == "bom" else BAD_PLANS[kind]
    b, info, plan, solved = solve_case(cat, count, raw)
    els = solved["elementos"]
    n = len(b["produtos"])
    for e in els:  # nada fora da peça
        x, y, w, h = e["b"]
        assert x >= layout.M - 2.01 and y >= layout.M - 2.01 and x + w <= 102.01 - layout.M and y + h <= 102.01 - layout.M, (e["papel"], e["b"])
    for e in els:  # hierarquia: protegidos nunca encolhem
        if e["papel"] in layout.PROTECTED:
            assert "_s" not in e
    mains = [e for e in els if e["papel"] == "preco_principal"]
    if n:
        assert len(mains) == 1
        assert mains[0]["b"][2] * mains[0]["b"][3] / 100 >= (4.5 if n == 1 else 3.0)  # preço principal grande (% da peça)
        hero = [e for e in els if e["papel"] == "heroi"]
        if plan["heroi"] in {i for i, v in info.items() if v["qualidade"] in ("boa", "cartao")}:
            assert hero and hero[0]["b"][2] * hero[0]["b"][3] / 100 >= (12 if n == 1 else 6)
            others = [e for e in els if e["papel"] == "produto"]
            for o in others:
                assert hero[0]["b"][2] * hero[0]["b"][3] >= 1.8 * o["b"][2] * o["b"][3]  # herói claramente maior
    else:
        assert not mains and not any(e["tipo"] == "produto" for e in els)
    # todo item aparece exatamente uma vez (foto+preço+nome, ou na lista)
    shown = [e.get("produto") for e in els if e["tipo"] == "preco"] + [i for e in els if e["tipo"] == "lista" for i in e["itens"]]
    assert sorted(shown) == list(range(n))
    # foto fraca nunca vira foto na arte (sem recorte, mas com resolução, entra em cartão)
    for e in els:
        if e["tipo"] == "produto":
            assert info[e["produto"]]["qualidade"] in ("boa", "cartao")
    # produtos e preços não invadem as zonas reservadas aos objetos do cenário
    for z in solved["zonas_objetos"]:
        for e in els:
            if e["tipo"] in ("produto", "lista") and e["papel"] not in layout.PROTECTED:
                ox, oy = layout.overlap(e["b"], z)
                assert ox * oy <= 0.15 * e["b"][2] * e["b"][3] + 1.0


@pytest.mark.parametrize("cat,count", [(c, n) for c in CASES for n in ("um", "varios", "nenhum")])
def test_no_repeated_texts(cat, count):
    for raw in [PLANS[cat], BAD_PLANS[3]]:
        b, _, plan, solved = solve_case(cat, count, raw)
        texts = [plan["titulo"]["txt"], plan["loja"], (plan["chamada"] or {}).get("txt"), (plan["selo"] or {}).get("txt")]
        norm = [plano._norm(t) for t in texts if t]
        assert len(norm) == len(set(norm)), texts
        assert not (plan["loja"] and plano._norm(plan["loja"]) == plano._norm(plan["titulo"]["txt"]))


def test_plan_rejects_unsafe_model_text_and_props():
    b, _ = briefing("supermercado", "varios")
    plan = plano.normalize(plano.parse(BAD_PLANS[3]), b, {})
    assert plan["chamada"] is None  # "50% OFF garantido": número e promessa
    assert plan["selo"] is None     # repetia o nome da loja
    assert plan["cenario"]["objetos"] == []  # embalagem/logo/letreiro nunca vão para o gerador
    truncated = plano.parse('{"heroi":1,"layout":{"titulo":"base","heroi":"esquerda"')
    assert truncated["heroi"] == 1 and truncated["layout"]["heroi"] == "esquerda"


@pytest.mark.parametrize("cat", list(CASES))
def test_scene_prompt_planned_with_layout(cat):
    b, _, plan, solved = solve_case(cat, "varios", PLANS[cat])
    p = cena.prompt(plan, solved, b)
    assert "sem nenhum texto" in p and "sem embalagens" in p
    for prod in b["produtos"]:  # nomes completos de produtos nunca vão para o gerador
        assert prod["nome"].lower() not in p.lower()
    assert b["loja"].lower() not in p.lower()
    for obj in plan["cenario"]["objetos"]:
        assert obj in p
    if solved["zonas_objetos"]:
        assert "x " in p  # o objeto vai para a zona prevista


def _scene_with_text(text_box=True):
    im = Image.new("RGB", SIZE, (20, 20, 30))
    d = ImageDraw.Draw(im)
    for y in range(SIZE[1]):
        d.line([(0, y), (SIZE[0], y)], fill=(20 + y // 20, 10, 40))
    if text_box:
        d.rectangle([700, 80, 1050, 400], fill=(220, 180, 60))
    return im


def test_scene_invented_text_regenerates_then_corrects():
    b, _, plan, solved = solve_case("supermercado", "um", PLANS["supermercado"])
    fake_ocr = lambda img: [(65.0, 6.0, 32.0, 23.0, "BLACK FRA", 0.93)]  # noqa: E731 — texto grande inventado
    rep = cena.verify(_scene_with_text(), plan, solved, fake_ocr, [plan["titulo"]["txt"]])
    assert any(p[0] == "texto_inventado" for p in rep["problemas"])
    assert cena.decide(rep, attempt=1, max_attempts=2) == "regerar"
    assert cena.decide(rep, attempt=2, max_attempts=2) == "corrigir"  # limite de tentativas
    small = cena.verify(_scene_with_text(False), plan, solved, lambda img: [(10.0, 90.0, 6.0, 2.0, "ab", 0.8)], [])
    assert cena.decide(small, attempt=1) == "corrigir"
    before = _scene_with_text()
    after = cena.soften_text(before, rep["textos"])
    W, H = SIZE
    box = (int(0.70 * W), int(0.10 * H), int(0.95 * W), int(0.27 * H))
    assert list(before.crop(box).getdata()) != list(after.crop(box).getdata())


def test_pipeline_regenerates_scene_with_limit():
    b, photos = briefing("tecnologia", "um")
    calls = []

    def gen(prompt, attempt):
        calls.append(prompt)
        return _scene_with_text()
    r = pipeline.run(b, photos, lambda m: json.dumps(PLANS["tecnologia"]), gen,
                     ocr=lambda img: [(65.0, 6.0, 32.0, 23.0, "PROMO", 0.9)], max_scene=2, max_repair=0)
    assert len(calls) == 2 and r["decisoes_cenario"] == ["regerar", "corrigir"]
    assert "nenhuma letra" in calls[1]


needs_chrome = pytest.mark.skipif(not valida.chrome(), reason="Chromium/Chrome não disponível")


@needs_chrome
@pytest.mark.parametrize("cat,count", [(c, n) for c in CASES for n in ("um", "varios", "nenhum")])
def test_end_to_end_objective_validation(cat, count, tmp_path):
    """Monta de verdade no Chromium e mede: nada escondido, cortado, fora da peça ou encavalado; hierarquia mínima."""
    b, photos = briefing(cat, count)
    r = pipeline.run(b, photos, lambda m: json.dumps(PLANS[cat]), lambda p, a: _scene_with_text(False), max_scene=1, max_repair=4)
    critical = [p for p in r["problemas_finais"] if p[0] in ("escondido", "fora_da_peca", "sobreposicao", "texto_cortado", "hierarquia_fraca")]
    assert not critical, critical
    assert r["avaliacao_estetica"] is None  # nenhuma nota subjetiva decide a aprovação


def test_objects_without_side_get_a_free_side():
    b, _ = briefing("supermercado", "um")
    raw = {"layout": {"heroi": "centro", "objetos": "nenhum"}, "cenario": {"objetos": ["xícara de café fumegante"]}}
    plan = plano.normalize(raw, b, {0: "boa"})
    assert plan["layout"]["objetos"] != "nenhum" and plan["cenario"]["objetos"]
    p = cena.prompt(plan, layout.solve(plan, b, {0: {"aspecto": 0.7, "qualidade": "boa"}}, SIZE), b)
    assert "café fumegante" in p  # palavra comum do tema não é apagada


def test_price_never_invents_cents():
    from studio.livre import render
    assert render._split_price("R$ 17,90") == ("17", ",90", "")
    assert render._split_price("R$ 1.800/mês") == ("1.800", "", "/mês")
    assert render._split_price("R$ 420.000") == ("420.000", "", "")


# ------------------------------------------------ legibilidade pelo desenho real (título e arco)
def _ink_case(text_rgb):
    bare = Image.new("RGB", (200, 100), (225, 235, 240))  # fundo claro
    full = bare.copy()
    ImageDraw.Draw(full).rectangle((40, 30, 160, 70), fill=text_rgb)
    items = [{"k": "titulo:", "x": 20, "y": 20, "w": 160, "h": 60}]
    return valida.ink(full, bare, items, (200, 100), {"titulo:"})["titulo:"]


def test_ink_measures_real_contrast_for_any_effect():
    assert _ink_case((250, 240, 190)) < valida.LEGIBLE_INK  # dourado claro sobre fundo claro: ilegível
    assert _ink_case((20, 20, 30)) > 0.9                    # escuro sobre claro: legível


def test_unreadable_title_gets_outline_then_plate_and_arc_never_deforms():
    solved = {"elementos": [{"tipo": "texto", "papel": "titulo", "b": [5, 3, 90, 20], "prio": 1, "min": 1, "z": 5,
                             "ef_plano": "arco", "_reforco_cor": "#111114"}], "zonas_objetos": []}
    items = [{"k": "titulo:", "x": 50, "y": 40, "w": 900, "h": 260, "bx": 50, "by": 40, "bw": 970, "bh": 270, "fonte": 120,
              "arco": True, "arco_escala": 0.5, "visivel": 1}]
    probs = valida.check(items, solved, (1080, 1350), None, 1, {"titulo:": 0.2})
    codes = {p[0] for p in probs}
    assert {"legibilidade", "letras_deformadas"} <= codes
    valida.repair(solved, probs, items, (1080, 1350))
    e = solved["elementos"][0]
    assert e["ef_forcado"] != "arco" and e["reforco"] == "#111114"  # sai do arco e ganha contorno
    valida.repair(solved, [("legibilidade", "titulo:", 0.2)], items, (1080, 1350))
    assert e["placa"]  # ainda ilegível: placa atrás do texto


# ------------------------------------------------ leitura do cenário, estética e variedade
from studio.livre import estetica, recursos  # noqa: E402


def _scene_with_object(box):
    img = Image.new("RGB", (1080, 1350), (232, 228, 220))
    d = ImageDraw.Draw(img)
    x, y, w, h = box
    for i in range(0, int(w * 10.8), 18):  # objeto com textura (bordas fortes)
        d.rectangle((x * 10.8 + i, y * 13.5, x * 10.8 + i + 9, (y + h) * 13.5), fill=(40, 30, 20))
    return img


def test_header_moves_off_scene_object_to_calm_area():
    smap = estetica.scene_map(_scene_with_object((10, 2, 80, 26)))  # objeto grande no alto
    solved = {"elementos": [{"tipo": "texto", "papel": "titulo", "b": [4, 4, 92, 18], "prio": 0, "min": 1, "z": 40},
                            {"tipo": "texto", "papel": "loja", "b": [18, 23, 64, 4.6], "prio": 5, "min": 0.8, "z": 40}],
              "zonas_objetos": []}
    before = estetica.busy(smap, solved["elementos"][0]["b"])
    moves = estetica.place(solved, smap)
    after = estetica.busy(smap, solved["elementos"][0]["b"])
    assert moves and after < before - 0.3
    t, lj = solved["elementos"]
    assert lj["b"][1] > t["b"][1]  # a ordem do cabeçalho é preservada (empresa continua abaixo do título)


def test_price_never_hides_product_or_name():
    items = [{"k": "heroi:0", "x": 300, "y": 300, "w": 400, "h": 500}, {"k": "preco_principal:0", "x": 320, "y": 450, "w": 380, "h": 250},
             {"k": "nome:0", "x": 300, "y": 640, "w": 400, "h": 60}]
    solved = {"elementos": [{"tipo": "produto", "papel": "heroi", "produto": 0, "b": [27.8, 22.2, 37, 37], "prio": 1, "min": 1, "z": 20},
                            {"tipo": "preco", "papel": "preco_principal", "produto": 0, "b": [29.6, 33.3, 35, 18.5], "prio": 0, "min": 1, "z": 45},
                            {"tipo": "nome", "papel": "nome", "produto": 0, "b": [27.8, 47.4, 37, 4.4], "prio": 4, "min": 0.75, "z": 44}],
              "zonas_objetos": []}
    probs = estetica.check(items, solved, (1080, 1350))
    codes = {p[0] for p in probs}
    assert {"preco_sobre_produto", "preco_sobre_nome"} <= codes
    assert estetica.repair(solved, probs, items, (1080, 1350))
    pb, hb = solved["elementos"][1]["b"], solved["elementos"][0]["b"]
    ox, oy = layout.overlap(pb, hb)
    assert ox * oy <= 0.18 * hb[2] * hb[3] + 1e-6 and pb[2] == 35  # saiu de cima da foto sem encolher


def test_typography_follows_declared_tone_and_avoids_recent_repeats():
    b = {"loja": "X", "titulo": "Grande Liquidação", "produtos": []}
    raw = {"tom": "impacto", "titulo": {"fonte": "playfair", "ef": "arco"}}
    n = plano.normalize(raw, b, {}, recent={"fontes": [], "efeitos": []})
    assert n["titulo"]["fonte"] in recursos.TONES["impacto"] and n["titulo"]["ef"] != "arco"  # título longo não vai em arco
    first = n["titulo"]["fonte"]
    n2 = plano.normalize({"tom": "impacto", "titulo": {"fonte": first, "ef": "neon"}}, b, {},
                         recent={"fontes": [first], "efeitos": ["neon"]})
    assert n2["titulo"]["fonte"] != first and n2["titulo"]["fonte"] in recursos.TONES["impacto"] and n2["titulo"]["ef"] != "neon"
    n3 = plano.normalize({"tom": "elegante", "titulo": {"fonte": "dmserif", "ef": "sombra"}}, b, {}, recent=None)
    assert n3["titulo"]["fonte"] == "dmserif"  # escolha coerente e nova: a decisão do diretor fica


def test_combined_plan_takes_structure_from_one_and_creativity_from_other():
    q = {"heroi": 1, "prioridades": ["preco", "produto"], "layout": {"heroi": "esquerda", "preco": "ao_lado_heroi", "objetos": "nenhum"},
         "preco": {"estilo": "bloco"}, "titulo": {"fonte": "playfair"}}
    g = {"heroi": 0, "titulo": {"fonte": "bangers", "ef": "3d"}, "chamada": {"txt": "Seu café"}, "layout": {"objetos": "direita"},
         "cenario": {"ambiente": "balcão"}}
    c = plano.combine(q, g)
    assert c["heroi"] == 1 and c["preco"]["estilo"] == "bloco" and c["layout"]["preco"] == "ao_lado_heroi"
    assert c["titulo"]["fonte"] == "bangers" and c["chamada"]["txt"] == "Seu café" and c["layout"]["objetos"] == "direita"


def test_photo_without_clean_background_becomes_card_not_text():
    img = Image.new("RGB", (800, 800), (90, 120, 60))
    ImageDraw.Draw(img).ellipse((200, 200, 600, 600), fill=(200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    assert pipeline.photo_info(buf.getvalue())["qualidade"] == "cartao"
    small = io.BytesIO()
    img.resize((200, 200)).save(small, "JPEG")
    assert pipeline.photo_info(small.getvalue())["qualidade"] == "fraca"  # pequena demais: continua texto


# ------------------------------------------------ motor dinâmico: fontes, efeitos por propriedades, layout
from studio.livre import efeitos, fontes_google, otimiza  # noqa: E402

CAT = [
    {"f": "Bold Shout", "c": "Display", "w": [400, 900], "lar": 6, "esp": 9, "pop": 50, "t": {"/Expressive/Loud": 0.9, "/Expressive/Excited": 0.7}},
    {"f": "Loud Two", "c": "Display", "w": [800], "lar": 5, "esp": 8, "pop": 80, "t": {"/Expressive/Loud": 0.8}},
    {"f": "Calm Serif", "c": "Serif", "w": [400, 700], "lar": 7, "esp": 4, "pop": 30, "t": {"/Expressive/Sophisticated": 0.9}},
    {"f": "Calm Serif Display", "c": "Serif", "w": [400], "lar": 7, "esp": 4, "pop": 40, "t": {"/Expressive/Sophisticated": 0.85}},
    {"f": "Thin Loud", "c": "Display", "w": [200], "lar": 7, "esp": 2, "pop": 10, "t": {"/Expressive/Loud": 0.9}},
]


def test_font_catalog_follows_described_personality_and_varies():
    pick = fontes_google.choose({"classe": "display", "peso": "ultra", "personalidade": ["impacto"]}, cat=CAT)
    assert pick["familia"] in ("Bold Shout", "Loud Two")  # pesada e expressiva; a fina não serve para "ultra"
    other = fontes_google.choose({"classe": "display", "peso": "ultra", "personalidade": ["impacto"]}, recent=[pick["familia"]], cat=CAT)
    assert other and other["familia"] != pick["familia"]  # não repete a última arte
    ele = [fontes_google.choose({"classe": "serifa", "personalidade": ["elegante"]}, seed=s, cat=CAT)["familia"] for s in range(4)]
    assert set(ele) == {"Calm Serif"}  # variantes da mesma família-base contam como uma só


def test_effect_properties_are_validated_and_compiled():
    fx = efeitos.normalize({"contornos": [{"espessura": 9, "cor": "javascript:alert(1)"}] * 5, "inclinacao": 80,
                            "profundidade": {"camadas": 99, "direcao": 135}, "preenchimento": {"tipo": "gradiente", "cores": ["#ff0000", "#220000"]}})
    assert len(fx["contornos"]) == 3 and fx["contornos"][0]["espessura"] == 0.12 and fx["inclinacao"] == 14
    assert fx["profundidade"]["camadas"] == 14 and fx["contornos"][0]["cor"].startswith("#")
    c = efeitos.css(fx, 200)
    assert "linear-gradient(" in c and "skewX(14.0deg)" in c and c.count("drop-shadow") <= efeitos.MAX_FILTERS
    a, b = efeitos.css(efeitos.preset("neon", "#00e5ff", "#000", seed=1), 200), efeitos.css(efeitos.preset("neon", "#00e5ff", "#000", seed=2), 200)
    assert a != b  # o mesmo estilo nomeado varia entre artes


def test_dynamic_layout_removes_overlaps_and_avoids_scene_objects():
    smap = estetica.scene_map(_scene_with_object((5, 2, 90, 11)))  # objeto no alto, espaço livre logo abaixo
    solved = {"elementos": [
        {"tipo": "texto", "papel": "titulo", "b": [4, 4, 92, 12], "prio": 0, "min": 1, "z": 40},
        {"tipo": "produto", "papel": "heroi", "produto": 0, "b": [30, 30, 36, 34], "prio": 1, "min": 1, "z": 20},
        {"tipo": "preco", "papel": "preco_principal", "produto": 0, "b": [52, 55, 34, 12], "prio": 0, "min": 1, "z": 45},
        {"tipo": "nome", "papel": "nome", "produto": 0, "b": [30, 65, 36, 5], "prio": 4, "min": 0.75, "z": 44},
        {"tipo": "lista", "papel": "lista", "b": [10, 66, 80, 12], "prio": 3, "min": 0.8, "z": 35, "itens": [1]}],
        "zonas_objetos": []}
    rep = otimiza.refine(solved, smap, (1080, 1350), seed=3)
    assert rep["custo_depois"] < rep["custo_antes"] and rep["depois"]["sobreposicao"] < rep["antes"]["sobreposicao"]
    t = solved["elementos"][0]["b"]
    assert estetica.busy(smap, t) < 0.4  # o título saiu de cima do objeto do cenário
    hero, price = solved["elementos"][1]["b"], solved["elementos"][2]["b"]
    assert abs((price[0] - hero[0]) - (52 - 30)) < 8  # preço continua junto do produto (o grupo anda junto)


def test_scene_with_blank_band_is_regenerated():
    img = Image.new("RGB", (1080, 1350), (120, 90, 60))
    d = ImageDraw.Draw(img)
    for i in range(0, 1080, 30):
        d.line((i, 0, i, 1000), fill=(30, 20, 10), width=6)
    d.rectangle((0, 1000, 1080, 1350), fill=(255, 255, 255))  # quarto de baixo branco chapado
    r = cena.verify(img, {"cenario": {"objetos": []}}, {"elementos": [], "zonas_objetos": []})
    assert any(p[0] == "area_vazia" for p in r["problemas"]) and cena.decide(r, 1) == "regerar"


# ------------------------------------------------ resolução pedida no chat
from studio.livre import formato  # noqa: E402


def test_resolution_from_natural_language():
    assert formato.parse("Crie uma arte para o feed do Instagram em 1080 × 1350.")[:2] == (1080, 1350)
    assert formato.parse("Quero um Story em 1080 x 1920")[:2] == (1080, 1920)
    assert formato.parse("banner horizontal de 1920 por 1080")[:2] == (1920, 1080)
    assert formato.parse("Quero uma imagem em 4K.")[:2] == (3840, 2160)
    assert formato.parse("story em 4k")[:2] == (2160, 3840)
    assert formato.parse("faz uma arte do meu petshop")[2] == "escolhida pelo contexto"
    assert formato.parse("imagem de 20000 x 10000")[:2] == (4096, 2048)  # limite do produto final


def test_generation_plan_never_stretches():
    for final in [(1080, 1350), (1080, 1920), (1920, 1080), (1024, 1024), (3840, 2160), (2480, 3508)]:
        g = formato.generation_plan(final)
        gw, gh = g["geracao"]
        assert gw * gh <= formato.GEN_MAX_PIXELS and gw % 64 == 0 and gh % 64 == 0
        assert abs(gw / gh - final[0] / final[1]) < 0.08  # mesma proporção (sem deformar)
        assert g["metodo"] == ("nativo" if g["fator"] <= 1.05 else "super_resolucao_2x" if g["fator"] <= 2.05 else "super_resolucao_4x")
