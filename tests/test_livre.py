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
        if plan["heroi"] in {i for i, v in info.items() if v["qualidade"] == "boa"}:
            assert hero and hero[0]["b"][2] * hero[0]["b"][3] / 100 >= (12 if n == 1 else 6)
            others = [e for e in els if e["papel"] == "produto"]
            for o in others:
                assert hero[0]["b"][2] * hero[0]["b"][3] >= 1.8 * o["b"][2] * o["b"][3]  # herói claramente maior
    else:
        assert not mains and not any(e["tipo"] == "produto" for e in els)
    # todo item aparece exatamente uma vez (foto+preço+nome, ou na lista)
    shown = [e.get("produto") for e in els if e["tipo"] == "preco"] + [i for e in els if e["tipo"] == "lista" for i in e["itens"]]
    assert sorted(shown) == list(range(n))
    # foto fraca nunca vira foto na arte
    for e in els:
        if e["tipo"] == "produto":
            assert info[e["produto"]]["qualidade"] == "boa"
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
    for prod in b["produtos"]:  # marcas/nomes de produtos nunca vão para o gerador
        assert prod["nome"].split()[0].lower() not in p.lower() or len(prod["nome"].split()[0]) <= 3
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
