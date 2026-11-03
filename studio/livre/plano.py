"""Plano semântico do diretor criativo: intenção, prioridades, zonas, relações, textos, estilo e cenário.

O modelo não dá coordenadas (o resolvedor cuida da geometria). Tudo o que vem do modelo é validado aqui: enums,
textos curtos sem números/promessas, ícones existentes, objetos do cenário que não imitam o produto, e um registro de
textos para nunca repetir título, nome da loja, chamada ou selo na mesma arte.
"""
import json
import re
import unicodedata

from studio import htmlflyer

from . import efeitos, recursos

ZONES_TITLE = ("topo", "base", "esquerda", "direita", "centro")
ZONES_HERO = ("centro", "esquerda", "direita")
PRICE_REL = ("abaixo_heroi", "ao_lado_heroi", "sobre_heroi", "base")
OTHERS_REL = ("fila_base", "coluna_lateral", "grade", "arco")
PROPS_SIDE = ("esquerda", "direita", "ambos", "atras", "nenhum")
SURFACES = ("pedestal", "bancada", "mesa", "prateleira", "nenhuma")
PRICE_STYLES = ("faixa", "etiqueta", "explosao", "circulo", "bloco")
SEAL_SHAPES = ("estrela", "circulo", "fita", "etiqueta", "carimbo", "balao", "hexagono", "escudo")
_BAD = re.compile(r"\d|r\$|%|grátis|gratis|garantid|melhor preço do|o mais barato", re.I)
_PROP_BAD = re.compile(r"embalag|pacote|logo|marca|r[óo]tulo|texto|letreiro|placa|letra|pre[çc]o|cartaz|etiqueta|caixa d[eo] ", re.I)

PLAN_PROMPT = """Você é o diretor criativo de uma agência. Planeje um anúncio PUBLICITÁRIO profissional (não um template) para o
briefing abaixo. Você decide a IDEIA, a hierarquia, o estilo, as relações entre os elementos e o CENÁRIO que será gerado
por IA; um resolvedor calcula a geometria. NÃO dê coordenadas.
Responda SOMENTE com JSON compacto (sem espaços):
{"ideia":"1 frase","tom":"{TONS}","heroi":N ou null,"prioridades":["preco","produto","titulo","chamada","selo","loja"] (ordem de importância),
 "layout":{"titulo":"topo|base|esquerda|direita|centro","heroi":"centro|esquerda|direita",
  "preco":"abaixo_heroi|ao_lado_heroi|sobre_heroi|base","demais":"fila_base|coluna_lateral|grade|arco",
  "objetos":"esquerda|direita|ambos|atras|nenhum"},
 "titulo":{"fonte_desc":{"classe":"serifa|sem_serifa|display|manuscrita|slab","peso":"leve|normal|pesado|ultra","largura":"condensada|normal|larga","personalidade":["2-3 palavras: impacto, elegante, retro, tecnologico, divertido, acolhedor, artesanal, luxo, rustico..."]},
  "efeito":{"preenchimento":{"tipo":"solido|gradiente|metalico","cores":["#hex"],"angulo":0-360},"contornos":[{"espessura":0.01-0.1,"cor":"#hex"}],
   "sombras":[{"x":-0.2-0.2,"y":-0.2-0.3,"desfoque":0-0.4,"cor":"#hex","opacidade":0-1}],"profundidade":{"camadas":0-12,"direcao":0-360,"cor":"#hex"},
   "brilho":{"cor":"#hex","intensidade":0-1},"inclinacao":-12-12,"rotacao":-6-6,"curva":0-1,"espacamento":-0.03-0.25} (só as propriedades que quiser),
  "cor":"#hex","cor2":"#hex","txt":"só se o briefing não tem título: até 4 palavras"},
 "chamada":{"txt":"até 4 palavras, sem números/preços/datas/promessas, diferente do título","fonte":"<chave>","ef":"<estilo>","cor":"#hex","cor2":"#hex"} ou null,
 "selo":{"txt":"até 3 palavras","f":"estrela|circulo|fita|etiqueta|carimbo|balao|hexagono|escudo","cor":"#hex","cor2":"#hex"} ou null,
 "preco":{"estilo":"faixa|etiqueta|explosao|circulo|bloco","fonte":"<chave>","bg":"#hex","fg":"#hex"},
 "nomes":{"fonte":"<chave>","cor":"#hex"},
 "icones":["até 3 da lista"],
 "cenario":{"ambiente":"descrição visual do fundo (cores, luz, clima, materiais)","superficie":"pedestal|bancada|mesa|prateleira|nenhuma",
  "objetos":["até 3 objetos decorativos ligados ao tema/produto, SEM embalagens, marcas ou textos"],"luz":"#hex da luz dominante"}}
"tom" = a personalidade desta campanha, lida do pedido (não do segmento): a fonte do título deve combinar com ele.
Título: DESCREVA a fonte (fonte_desc) e o efeito por propriedades; o Studio escolhe a família num catálogo com mais de
mil fontes. Para chamada e nomes, use uma chave: {FONTES}
Estilos de escrita: neon, metal, 3d, adesivo, gradiente, contorno, faixa, arco, italico, sombra (título e chamada com estilos diferentes).
Ícones: {ICONES}
Princípios: hierarquia forte (no varejo o preço principal e o produto herói dominam; em serviços/institucional, o título e a
chamada); poucos elementos bem grandes valem mais que muitos pequenos; o cenário conta a história (ambiente e objetos do tema);
contraste alto; nada repetido (não repita o nome da loja nem o título em outros textos)."""


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class TextRegistry:
    """Textos já presentes na arte: impede repetição (título = loja, chamada = título, selo = chamada...)."""

    def __init__(self):
        self.seen = []

    def has(self, text):
        n = _norm(text)
        if not n:
            return True
        return any(n == s or (len(n) > 3 and (n in s or s in n)) for s in self.seen)

    def add(self, text):
        n = _norm(text)
        if n and not self.has(text):
            self.seen.append(n)
            return True
        return False


def _short(txt, words):
    txt = re.sub(r"\s+", " ", str(txt or "")).strip(" .!-")
    if not txt or _BAD.search(txt) or len(txt.split()) > words or len(txt) > 32:
        return ""
    return txt


def _enum(v, options, default):
    return v if v in options else default


def _style(d, default_font, default_ef, default_color, default_c2):
    d = d if isinstance(d, dict) else {}
    return {"fonte": _enum(d.get("fonte"), recursos.FONTS, default_font), "ef": _enum(d.get("ef"), recursos.EFFECTS, default_ef),
            "cor": htmlflyer._hex(d.get("cor"), default_color), "cor2": htmlflyer._hex(d.get("cor2"), default_c2)}


def _seed(briefing):
    return sum(ord(c) for c in json.dumps(briefing, ensure_ascii=False, sort_keys=True, default=str)) % 100003


def plan_messages(briefing, photo_quality, recent=None):
    """Mensagens para o diretor criativo. photo_quality: {indice: 'boa'|'fraca'|'cartao'|None}.
    recent: {"fontes": [...], "efeitos": [...]} das últimas artes (mais recente primeiro), para variar."""
    lines = []
    for i, p in enumerate(briefing.get("produtos") or []):
        q = photo_quality.get(i)
        foto = {"boa": "foto recortada boa", "cartao": "foto sem recorte (vai em cartão)",
                "fraca": "foto fraca (vira texto)"}.get(q, "sem foto")
        extra = f" (de {p['preco_de']})" if p.get("preco_de") else ""
        lines.append(f"{i}: {p['nome']} — {p.get('preco') or 'sem preço'}{extra} — {foto}")
    user = "\n".join(x for x in [
        f"Formato: {briefing.get('formato') or 'feed_4x5'}", f"Empresa: {briefing.get('loja') or '(não informada)'}",
        f"Segmento: {briefing.get('segmento') or '(deduza)'}", f"Título oficial: {briefing.get('titulo') or '(nenhum)'}",
        f"Pedido do cliente: {briefing.get('pedido') or briefing.get('tema') or '(nenhum)'}",
        ("Itens:\n" + "\n".join(lines)) if lines else "Sem produtos: peça institucional/serviço.",
        f"Informações oficiais extras: {json.dumps(briefing.get('info'), ensure_ascii=False)}" if briefing.get("info") else "",
    ] if x)
    if recent and (recent.get("fontes") or recent.get("efeitos")):
        user += ("\nVarie: as últimas artes já usaram as fontes " + ", ".join(recent.get("fontes", [])[:4])
                 + " e os estilos " + ", ".join(recent.get("efeitos", [])[:3]) + "; escolha outros que combinem com o tom.")
    system = (PLAN_PROMPT.replace("{FONTES}", recursos.font_menu(_seed(briefing)))
              .replace("{ICONES}", ", ".join(sorted(recursos.ICONS))).replace("{TONS}", "|".join(recursos.TONES)))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse(text):
    """JSON do modelo; aceita JSON truncado (fecha chaves/colchetes do que veio completo)."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    s = str(text or "")
    for cut in range(len(s), 0, -1):
        if s[cut - 1] not in "}]\"0123456789el":
            continue
        frag = s[:cut]
        opens = []
        instr = False
        for ch in frag:
            if ch == '"':
                instr = not instr
            elif not instr and ch in "{[":
                opens.append(ch)
            elif not instr and ch in "}]" and opens:
                opens.pop()
        if instr:
            continue
        try:
            return json.loads(frag + "".join("}" if o == "{" else "]" for o in reversed(opens)))
        except ValueError:
            continue
    return {}


def _vary_type(raw, briefing, recent):
    """Tipografia coerente com o tom declarado e sem repetir as últimas artes. A intenção do diretor (o tom) fica;
    só a família muda, para outra do MESMO tom (nunca uma regra por segmento)."""
    tit = raw.get("titulo") if isinstance(raw.get("titulo"), dict) else {}
    tone = raw.get("tom") if raw.get("tom") in recursos.TONES else None
    used = list((recent or {}).get("fontes") or [])[:3]
    used_fx = list((recent or {}).get("efeitos") or [])[:2]
    font = tit.get("fonte") if tit.get("fonte") in recursos.FONTS else None
    if tone:
        pool = list(recursos.TONES[tone])
    else:  # sem tom declarado: o tom implícito é o da fonte escolhida
        pool = next((list(g) for g in recursos.TONES.values() if font in g), [])
    seed = _seed(briefing)
    if font and (font in used or (pool and font not in pool)):
        free = [f for f in pool if f not in used and f != font] or [f for f in pool if f != font]
        if free:
            font = free[seed % len(free)]
    elif not font and pool:
        font = pool[seed % len(pool)]
    ef = tit.get("ef") if tit.get("ef") in recursos.EFFECTS else None
    title_len = len((briefing.get("titulo") or tit.get("txt") or "").strip())
    if ef == "arco" and title_len > 16:
        ef = None  # arco só para títulos curtos (texto longo no arco deforma ou fica pequeno)
    if ef is None or ef in used_fx:
        # substituto nunca é "faixa" (caixa atrás do texto): só o diretor escolhe esse estilo de propósito
        options = [x for x in recursos.EFFECTS if x not in used_fx and x != ef and x != "faixa" and (x != "arco" or title_len <= 16)]
        ef = options[(seed // 7) % len(options)] if options else ef
    out = {**tit, "fonte": font or tit.get("fonte"), "ef": ef}
    ch = raw.get("chamada") if isinstance(raw.get("chamada"), dict) else None
    if ch and ch.get("fonte") == out["fonte"]:  # título e chamada com fontes diferentes (contraste tipográfico)
        alt = [f for f in recursos.BODY_FONTS + ("satisfy", "righteous", "bebas") if f != out["fonte"]]
        ch = {**ch, "fonte": alt[seed % len(alt)]}
    return {**raw, "titulo": out, **({"chamada": ch} if ch else {})}, tone


def _dynamic_title(raw, briefing, recent):
    """Fonte do catálogo amplo (descrita pelo diretor) e efeito por propriedades. Sem descrição: nada muda."""
    tit = raw.get("titulo") if isinstance(raw.get("titulo"), dict) else {}
    desc = tit.get("fonte_desc")
    out = {}
    if isinstance(desc, dict):
        try:
            from . import fontes_google  # noqa: PLC0415
            pick = fontes_google.choose(desc, recent=[f[2:] for f in (recent or {}).get("fontes", [])[:6] if str(f).startswith("g:")],
                                        seed=_seed(briefing))
        except Exception:  # noqa: BLE001 — catálogo indisponível: segue com o catálogo local
            pick = None
        if pick:
            out.update({"fonte": "g:" + pick["familia"], "peso": pick["peso"], "fonte_motivo": pick["motivo"]})
    return out


def normalize(raw, briefing, photo_quality, recent=None):
    """Plano canônico e seguro, com padrões sensatos para o que faltar. Nunca altera dados comerciais.
    recent: fontes/efeitos das últimas artes (variedade entre peças)."""
    raw = raw if isinstance(raw, dict) else {}
    dyn = _dynamic_title(raw, briefing, recent)
    raw, tone = _vary_type(raw, briefing, recent)
    prods = briefing.get("produtos") or []
    n = len(prods)
    good = [i for i in range(n) if photo_quality.get(i) in ("boa", "cartao")]
    lay = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}

    hero = raw.get("heroi")
    hero = hero if isinstance(hero, int) and 0 <= hero < n else None
    if n and hero is None:
        # padrão: oferta com "de/por" ou com foto boa; senão o primeiro
        hero = next((i for i in good if prods[i].get("preco_de")), None)
        hero = hero if hero is not None else (good[0] if good else 0)
    if hero is not None and good and hero not in good and photo_quality.get(hero) not in ("boa", "cartao"):
        # herói sem foto boa: se houver outro com foto e o modelo não insistiu em oferta, usa a foto boa
        if not prods[hero].get("preco_de"):
            hero = good[0]

    prio = [p for p in (raw.get("prioridades") or []) if p in ("preco", "produto", "titulo", "chamada", "selo", "loja")]
    for p in (["preco", "produto", "titulo", "chamada", "selo", "loja"] if n else ["titulo", "chamada", "selo", "loja"]):
        if p not in prio:
            prio.append(p)

    reg = TextRegistry()
    for p in prods:
        reg.add(p["nome"])
    loja = (briefing.get("loja") or "").strip()
    titulo_txt = (briefing.get("titulo") or "").strip()
    if not titulo_txt:
        titulo_txt = _short((raw.get("titulo") or {}).get("txt") if isinstance(raw.get("titulo"), dict) else "", 4)
    if titulo_txt and loja and _norm(titulo_txt) == _norm(loja):
        titulo_txt = ""  # título igual ao nome da empresa: a empresa vira o título (uma vez só)
    if not titulo_txt:
        titulo_txt, loja_line = loja or "Ofertas", ""
    else:
        loja_line = loja
    reg.add(titulo_txt)
    if loja_line and not reg.add(loja_line):
        loja_line = ""

    ch = raw.get("chamada") if isinstance(raw.get("chamada"), dict) else None
    chamada = None
    if ch:
        txt = _short(ch.get("txt"), 4)
        if txt and reg.add(txt):
            chamada = {"txt": txt, **_style(ch, "satisfy", "sombra", "#ffffff", "#1e1e22")}
    se = raw.get("selo") if isinstance(raw.get("selo"), dict) else None
    selo = None
    if se:
        txt = _short(se.get("txt"), 3)
        if txt and reg.add(txt):
            selo = _vary_seal({"txt": txt, "forma": _enum(se.get("f"), SEAL_SHAPES, "estrela"), "cor": htmlflyer._hex(se.get("cor"), "#ffd400"),
                               "cor2": htmlflyer._hex(se.get("cor2"), "#111111")}, raw, briefing, recent)

    pr = raw.get("preco") if isinstance(raw.get("preco"), dict) else {}
    preco = {"estilo": _enum(pr.get("estilo"), PRICE_STYLES, "faixa"),
             "fonte": _enum(pr.get("fonte"), recursos.PRICE_FONTS, "anton"),
             "bg": htmlflyer._hex(pr.get("bg"), "#ffd400")}
    preco["fg"] = htmlflyer._hex(pr.get("fg"), htmlflyer._on(preco["bg"]))
    if _contrast_hex(preco["fg"], preco["bg"]) < 3:
        preco["fg"] = htmlflyer._on(preco["bg"])
    nm = raw.get("nomes") if isinstance(raw.get("nomes"), dict) else {}
    nomes = {"fonte": _enum(nm.get("fonte"), recursos.BODY_FONTS, "poppins"), "cor": htmlflyer._hex(nm.get("cor"), "#ffffff")}

    cen = raw.get("cenario") if isinstance(raw.get("cenario"), dict) else {}
    # objeto que repete o produto (ex.: "pacote de Café Pilão") seria uma embalagem inventada; palavra comum ("café") pode
    name_heads = {" ".join(_norm(p["nome"]).split()[:2]) for p in prods if len(_norm(p["nome"]).split()) >= 2}
    objetos = []
    for o in (cen.get("objetos") or [])[:3]:
        o = re.sub(r"\s+", " ", str(o or "")).strip()[:60]
        if o and not _PROP_BAD.search(o) and not any(h and h in _norm(o) for h in name_heads):
            objetos.append(o)
    props = _enum(lay.get("objetos"), PROPS_SIDE, "nenhum")
    if not objetos:
        props = "nenhum"
    elif props == "nenhum":
        # o diretor pediu objetos mas não disse onde: ficam do lado oposto ao herói (ou atrás, se o herói é central)
        hz = _enum(lay.get("heroi"), ZONES_HERO, "centro")
        props = {"esquerda": "direita", "direita": "esquerda"}.get(hz, "ambos" if n else "atras")
    icones = [i for i in (raw.get("icones") or []) if isinstance(i, str) and i in recursos.ICONS][:3]

    title_zone = _enum(lay.get("titulo"), ZONES_TITLE, "topo" if n else "centro")
    if n and title_zone == "centro":
        title_zone = "topo"  # com produtos, o centro é do herói
    return {
        "ideia": str(raw.get("ideia") or "")[:200], "tom": tone, "heroi": hero, "prioridades": prio,
        "layout": {"titulo": title_zone, "heroi": _enum(lay.get("heroi"), ZONES_HERO, "centro"),
                   "preco": _enum(lay.get("preco"), PRICE_REL, "abaixo_heroi"),
                   "demais": _enum(lay.get("demais"), OTHERS_REL, "fila_base"), "objetos": props},
        "titulo": _title_style(raw, titulo_txt, dyn, briefing),
        "loja": loja_line, "chamada": chamada, "selo": selo, "preco": preco, "nomes": nomes, "icones": icones,
        "cenario": {"ambiente": re.sub(r"\s+", " ", str(cen.get("ambiente") or "")).strip()[:260],
                    "superficie": _enum(cen.get("superficie"), SURFACES, "pedestal" if n == 1 else "bancada" if n else "nenhuma"),
                    "objetos": objetos, "luz": htmlflyer._hex(cen.get("luz"), "#ffd27a")},
        "textos": list(reg.seen),
    }


def _hue(hx):
    h = htmlflyer._hex(hx, "#ffffff")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 0.15:
        return "neutro"
    hue = ((g - b) / (mx - mn)) % 6 if mx == r else (b - r) / (mx - mn) + 2 if mx == g else (r - g) / (mx - mn) + 4
    return int(hue * 60 // 40)


def _vary_seal(selo, raw, briefing, recent):
    """Selo sem repetir as últimas artes: outra forma (o diretor escolheu a família visual; a forma gira) e, se a cor
    repetir, uma cor da PRÓPRIA paleta da arte. Inclinação também varia."""
    seed = _seed(briefing)
    last = list((recent or {}).get("selos") or [])[:3]
    if selo["forma"] in [x[0] for x in last if x]:
        free = [f for f in SEAL_SHAPES if f not in [x[0] for x in last if x]]
        selo["forma"] = free[seed % len(free)] if free else selo["forma"]
    if _hue(selo["cor"]) in [_hue(x[1]) for x in last if x]:
        pal = []
        for sec, keys in (("preco", ("bg",)), ("titulo", ("cor2", "cor")), ("chamada", ("cor",))):
            d = raw.get(sec) if isinstance(raw.get(sec), dict) else {}
            pal += [htmlflyer._hex(d.get(k), "") for k in keys if d.get(k)]
        pal = [c for c in pal if c and _hue(c) not in [_hue(x[1]) for x in last if x] and _hue(c) != "neutro"]
        if pal:
            selo["cor"] = pal[0]
            selo["cor2"] = htmlflyer._on(pal[0])
    selo["giro"] = (-12, -7, -4, 5, 9)[seed % 5]
    return selo


def _title_style(raw, txt, dyn, briefing):
    st = {"txt": txt, **_style(raw.get("titulo"), "anton", "sombra", "#ffffff", "#d71920"), **dyn}
    tit = raw.get("titulo") if isinstance(raw.get("titulo"), dict) else {}
    if isinstance(tit.get("efeito"), dict):
        st["efx"] = efeitos.normalize(tit["efeito"], st["cor"], st["cor2"])
        if st["efx"]["preenchimento"]["cores"]:
            st["cor"] = st["efx"]["preenchimento"]["cores"][0]
        st["ef"] = "arco" if st["efx"]["curva"] >= 0.45 and len(txt) <= 16 else "propriedades"
    elif dyn:  # fonte do catálogo amplo sem efeito descrito: receita do estilo nomeado, com variação
        st["efx"] = efeitos.preset(st["ef"], st["cor"], st["cor2"], _seed(briefing))
    return st


def _contrast_hex(a, b):
    def rel(h):
        out = []
        for i in (1, 3, 5):
            v = int(h[i:i + 2], 16) / 255
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    la, lb = rel(a), rel(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


STRUCTURE_KEYS = ("heroi", "prioridades", "layout", "preco")


def combine(structure, creative):
    """Plano híbrido: a ESTRUTURA (herói, prioridades, relações, estilo do preço) de um diretor e a parte CRIATIVA
    (ideia, tom, textos, tipografia, selo, ícones, cenário) de outro. Os dois brutos passam depois pelo normalize."""
    structure = structure if isinstance(structure, dict) else {}
    creative = creative if isinstance(creative, dict) else {}
    out = {k: v for k, v in creative.items() if k not in STRUCTURE_KEYS}
    for k in STRUCTURE_KEYS:
        if k in structure:
            out[k] = structure[k]
        elif k in creative:
            out[k] = creative[k]
    lay = dict(out.get("layout") or {}) if isinstance(out.get("layout"), dict) else {}
    if isinstance(creative.get("layout"), dict) and creative["layout"].get("objetos"):
        lay["objetos"] = creative["layout"]["objetos"]  # objetos do cenário pertencem à ideia criativa
    out["layout"] = lay
    return out
