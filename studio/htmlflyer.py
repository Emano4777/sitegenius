"""Montagem profissional do encarte em HTML/CSS (renderizada pelo Chromium das ferramentas da HostGator).

O diretor de arte (modelo) decide o DESIGN; este módulo traduz o design numa página com grade que se ajusta ao espaço,
fotos protagonistas, sombras, tipografia que encolhe até caber e elementos gráficos. Os dados (produtos, preços, fotos,
loja) vêm do briefing, sem alteração.

design = {
  "paleta": {"primaria": "#d71920", "secundaria": "#ffcc00", "fundo": "#fff6d6", "texto": "#1e1e22", "preco": "#d71920"},
  "fundo": "gradiente|listras|raios|bolinhas|diagonal|liso",
  "cabecalho": "faixa|diagonal|curva|selo",           "titulo_fonte": "anton|bebas|archivo|montserrat|playfair",
  "grade": "uniforme|destaque|mosaico|diagonal|lista", "destaque": "nome do produto" | null,
  "cartao": "branco|sem_cartao|vidro|borda",          "preco_estilo": "etiqueta|selo|bloco|faixa",
  "decoracao": ["estrelas", "confete", "circulos", "faixas"],  "densidade": "compacta|equilibrada|arejada",
  "chamada": "texto curto da tarja (opcional; só genérico ou vindo do cliente)",
}
"""
import base64
import html
import math
import os

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = {
    "anton": ("Anton-Regular.ttf", "Anton"), "bebas": ("BebasNeue-Regular.ttf", "Bebas Neue"),
    "archivo": ("ArchivoBlack-Regular.ttf", "Archivo Black"), "montserrat": ("Montserrat-VF.ttf", "Montserrat"),
    "playfair": ("PlayfairDisplay-VF.ttf", "Playfair Display"), "poppins": ("Poppins-Bold.ttf", "Poppins"),
    "poppins_x": ("Poppins-ExtraBold.ttf", "PoppinsX"), "poppins_r": ("Poppins-SemiBold.ttf", "PoppinsS"),
    "orbitron": ("Orbitron-VF.ttf", "Orbitron"),
}
DEFAULT = {
    "paleta": {"primaria": "#d71920", "secundaria": "#ffcc00", "fundo": "#fff4cc", "texto": "#1e1e22", "preco": "#d71920"},
    "fundo": "gradiente", "cabecalho": "diagonal", "titulo_fonte": "anton", "grade": "uniforme", "destaque": None,
    "cartao": "branco", "preco_estilo": "etiqueta", "decoracao": ["estrelas"], "densidade": "equilibrada", "chamada": "",
    "composicao": "auto", "proporcao_destaque": 0.56, "angulo": -8, "sobreposicao": 0.05, "estilo_unidade": "cartao",
}
SIZES = {"story": (1080, 1920), "feed_4x5": (1080, 1350), "quadrado": (1080, 1080), "a4": (1240, 1754)}


def _hex(c, default="#000000"):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c[:3])
    if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
        return c
    return default


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luma(h):
    r, g, b = _rgb(h)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _mix(a, b, t):
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ra, rb))


def _on(bg, light="#ffffff", dark="#1e1e22"):
    return dark if _luma(bg) > 160 else light


def normalize(design):
    """Valida o design do modelo (valores conhecidos, cores válidas); o que faltar vem do padrão."""
    d = {**DEFAULT, **{k: v for k, v in (design or {}).items() if k in DEFAULT}}
    pal = {**DEFAULT["paleta"], **{k: _hex(v, DEFAULT["paleta"][k]) for k, v in (d.get("paleta") or {}).items()
                                   if k in DEFAULT["paleta"]}}
    d["paleta"] = pal
    allowed = {"fundo": ("gradiente", "listras", "raios", "bolinhas", "diagonal", "liso"),
               "cabecalho": ("faixa", "diagonal", "curva", "selo"),
               "titulo_fonte": ("anton", "bebas", "archivo", "montserrat", "playfair", "orbitron"),
               "grade": ("uniforme", "destaque", "mosaico", "diagonal", "lista"),
               "cartao": ("branco", "sem_cartao", "vidro", "borda"),
               "preco_estilo": ("etiqueta", "selo", "bloco", "faixa"),
               "densidade": ("compacta", "equilibrada", "arejada")}
    for k, vals in allowed.items():
        if d.get(k) not in vals:
            d[k] = DEFAULT[k]
    d["decoracao"] = [x for x in (d.get("decoracao") or []) if x in ("estrelas", "confete", "circulos", "faixas")][:3]
    from .htmllayout import COMPOSITIONS  # noqa: PLC0415

    if d.get("composicao") not in COMPOSITIONS + ("auto",):
        d["composicao"] = "auto"
    d["estilo_unidade"] = d["estilo_unidade"] if d.get("estilo_unidade") in ("cartao", "solto") else "cartao"
    for k, lo, hi in (("proporcao_destaque", 0.4, 0.72), ("angulo", -14, 14), ("sobreposicao", 0, 0.12)):
        try:
            d[k] = max(lo, min(hi, float(d.get(k))))
        except (TypeError, ValueError):
            d[k] = DEFAULT[k]
    d["chamada"] = " ".join(str(d.get("chamada") or "").split())[:60]
    return d


def _font_faces():
    out = []
    for key, (file, family) in FONTS.items():
        path = os.path.join(FONT_DIR, file)
        if os.path.exists(path):
            data = base64.b64encode(open(path, "rb").read()).decode()
            weight = "font-weight:100 900;" if file.endswith("-VF.ttf") else ""  # fontes variáveis: todos os pesos
            out.append(f"@font-face{{font-family:'{family}';src:url(data:font/ttf;base64,{data});{weight}font-display:block}}")
    return "\n".join(out)


def _price_html(price, price_de, unit):
    value = (price or "").replace("R$", "").strip()
    whole, _, cents = value.partition(",")
    old = f'<div class="de">de {html.escape(price_de)}</div>' if price_de else ""
    unit_html = f'<div class="un">{html.escape(unit)}</div>' if unit else ""
    if not whole:
        return f'<div class="preco vazio">preço a definir</div>'
    return (f'{old}<div class="preco"><span class="rs">R$</span><span class="int fitw">{html.escape(whole)}</span>'
            f'<span class="cts">,{html.escape(cents or "00")}</span></div>{unit_html}')


def _discount(price, price_de):
    def cents(v):
        v = (v or "").replace("R$", "").replace(".", "").strip().replace(",", ".")
        try:
            return round(float(v) * 100)
        except ValueError:
            return 0
    now_c, old_c = cents(price), cents(price_de)
    if now_c and old_c > now_c:
        pct = round((old_c - now_c) * 100 / old_c)
        if 5 <= pct <= 90:
            return f"-{pct}%"
    return ""


def _grid(n, W, H, d, hero):
    """Colunas e linhas que deixam as fotos maiores para a área disponível."""
    if d["grade"] == "lista":
        return 1, n
    best = None
    aspect = W / max(1, H)
    for cols in range(1, 5):
        cells = n + (1 if hero and cols > 1 else 0)  # destaque ocupa 2 células
        rows = math.ceil(cells / cols)
        cw, ch = W / cols, H / rows
        vertical = min(cw * 0.9, ch * 0.55) ** 2           # foto em cima, preço embaixo
        horizontal = min(cw * 0.5, ch * 0.88) ** 2 if cw > ch * 1.25 else 0  # foto ao lado, preço à direita
        empty = (cols * rows - cells) / (cols * rows)
        score = max(vertical, horizontal) * (1 - 0.8 * empty) * (1.0 if cw > 260 else 0.4)
        if best is None or score > best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def build(briefing, photos, design):
    """HTML completo da peça. photos: {nome_do_produto: data-uri (PNG/JPEG)}."""
    d = normalize(design)
    p = d["paleta"]
    W, H = SIZES.get(briefing.get("formato"), SIZES["feed_4x5"])
    products = list(briefing.get("produtos") or [])
    hero_name = d.get("destaque") or (briefing.get("layout") or {}).get("destaque")
    hero = next((x for x in products if hero_name and x["nome"].lower() == str(hero_name).lower()), None)
    if d["grade"] == "destaque" and not hero and len(products) >= 3:
        hero = products[0]
    if hero:
        products.remove(hero)
        products.insert(0, hero)
    head_h = int(H * (0.16 if W / H < 0.7 else 0.19))
    foot_h = int(H * 0.05)
    pad = {"compacta": 22, "equilibrada": 34, "arejada": 48}[d["densidade"]]
    gap = {"compacta": 14, "equilibrada": 22, "arejada": 32}[d["densidade"]]
    area_w, area_h = W - 2 * pad, H - head_h - foot_h - 2 * pad - 20
    cols, rows = _grid(len(products), area_w, area_h, d, bool(hero))
    wide = (area_w / cols) > (area_h / rows) * 1.25  # células largas: cartão horizontal (foto à esquerda)
    title_font = FONTS[d["titulo_fonte"]][1]
    on_primary = _on(p["primaria"])
    loja = html.escape(briefing.get("loja") or "")
    titulo = html.escape((briefing.get("titulo") or "Ofertas").strip())
    chamada = html.escape(d["chamada"] or (f"Ofertas válidas {briefing['validade']}" if briefing.get("validade") else ""))
    validade = html.escape(f"Ofertas válidas {briefing['validade']}" if briefing.get("validade") else (loja and f"Ofertas • {loja}"))

    comp = d["composicao"]
    if comp == "auto":
        n_photo = sum(1 for x in products if photos.get(x["nome"]))
        comp = ("heroi_lateral" if n_photo == 3 else "heroi_topo" if n_photo in (2, 4) else "mosaico" if n_photo <= 6
                else "grade") if n_photo else "grade"
    use_free = comp in ("heroi_lateral", "heroi_topo", "diagonal", "mosaico", "vitrine") or \
        any(not photos.get(x["nome"]) for x in products)
    cards = []
    free_units = []
    if use_free:
        from .htmllayout import plan_units  # noqa: PLC0415

        has_photo = [bool(photos.get(x["nome"])) for x in products]
        free_units = plan_units(products, has_photo, (pad, head_h + pad + 10, area_w, area_h), comp,
                                {"destaque": hero["nome"] if hero else d.get("destaque"), "gap": gap,
                                 "proporcao_destaque": d["proporcao_destaque"], "angulo": d["angulo"],
                                 "sobreposicao": d["sobreposicao"], "estilo_unidade": d["estilo_unidade"]})
    for u in free_units:
        prod = products[u["indice"]]
        ref = u["h"] if u["tipo"] == "tipografica" else min(u["w"], u["h"] * (1.25 if u["tipo"] == "foto_horizontal" else 1.0))
        style_pos = (f"left:{u['x']}px;top:{u['y']}px;width:{u['w']}px;height:{u['h']}px;z-index:{u['z']};--s:{int(ref)}px;"
                     + (f"transform:rotate({u['rot']}deg);" if u["rot"] else ""))
        offer = f'<div class="oferta">{html.escape(prod["oferta"])}</div>' if prod.get("oferta") else ""
        price = f'<div class="precobox">{_price_html(prod.get("preco"), prod.get("preco_de"), prod.get("unidade"))}</div>'
        disc = _discount(prod.get("preco"), prod.get("preco_de"))
        sticker = f'<div class="selo-desc">{disc}</div>' if disc else ""
        name = f'<div class="nome fit">{html.escape(prod["nome"])}</div>'
        kind = u["tipo"]
        if kind == "tipografica":  # sem foto: nome + preço em destaque, compacto, sem caixa vazia
            cards.append(f'<div class="u tipo" style="{style_pos}">{sticker}<div class="tipo-nome fit">'
                         f'{html.escape(prod["nome"])}</div><div class="tipo-preco">{offer}{price}</div></div>')
            continue
        img = f'<img src="{photos[prod["nome"]]}">'
        hero_cls = " heroi" if u["heroi"] else ""
        if kind == "foto_solta":  # recorte solto, selo de preço sobreposto à foto
            cards.append(f'<div class="u solta{hero_cls}" style="{style_pos}">{sticker}<div class="foto">{img}'
                         f'<div class="chao"></div><div class="preco-sobre">{offer}{price}</div></div>{name}</div>')
        elif kind == "foto_horizontal":
            cards.append(f'<div class="u card horiz{hero_cls}" style="{style_pos}">{sticker}<div class="foto">{img}</div>'
                         f'<div class="info">{name}{offer}{price}</div></div>')
        else:
            cards.append(f'<div class="u card{hero_cls}" style="{style_pos}">{sticker}<div class="foto">{img}</div>'
                         f'<div class="info">{name}{offer}{price}</div></div>')
    for i, prod in enumerate(products if not use_free else []):
        is_hero = hero is not None and i == 0 and cols > 1
        span = f"grid-column: span {min(2, cols)};" if is_hero else ""
        photo = photos.get(prod["nome"])
        img = (f'<div class="foto"><img src="{photo}"></div>' if photo else
               f'<div class="foto semfoto"><span class="fit">{html.escape(prod["nome"])}</span></div>')
        offer = f'<div class="oferta">{html.escape(prod["oferta"])}</div>' if prod.get("oferta") else ""
        disc = _discount(prod.get("preco"), prod.get("preco_de"))
        sticker = f'<div class="selo-desc">{disc}</div>' if disc else ""
        tilt = ""
        if d["grade"] == "diagonal":
            tilt = f"transform: rotate({-3 if i % 2 == 0 else 2.5}deg);"
        cards.append(
            f'<div class="card {"hero" if is_hero or wide else ""}" style="{span}{tilt}">{sticker}{img}'
            f'<div class="info"><div class="nome fit">{html.escape(prod["nome"])}</div>{offer}'
            f'<div class="precobox">{_price_html(prod.get("preco"), prod.get("preco_de"), prod.get("unidade"))}</div></div></div>')

    bg = {
        "gradiente": f"radial-gradient(circle at 30% 20%, {_mix(p['fundo'], '#ffffff', .55)} 0%, {p['fundo']} 45%, "
                     f"{_mix(p['fundo'], p['secundaria'], .45)} 100%)",
        "listras": f"repeating-linear-gradient(-35deg, {p['fundo']} 0 46px, {_mix(p['fundo'], p['secundaria'], .35)} 46px 92px)",
        "raios": f"repeating-conic-gradient(from 0deg at 50% 38%, {p['fundo']} 0deg 9deg, "
                 f"{_mix(p['fundo'], p['secundaria'], .4)} 9deg 18deg)",
        "bolinhas": f"radial-gradient({_mix(p['fundo'], p['secundaria'], .5)} 18%, transparent 19%) 0 0/46px 46px, {p['fundo']}",
        "diagonal": f"linear-gradient(160deg, {p['fundo']} 0 55%, {_mix(p['primaria'], p['fundo'], .82)} 55% 100%)",
        "liso": p["fundo"],
    }[d["fundo"]]
    head_shape = {"faixa": "polygon(0 0,100% 0,100% 100%,0 100%)", "diagonal": "polygon(0 0,100% 0,100% 78%,0 100%)",
                  "curva": "ellipse(90% 100% at 50% 0)", "selo": "polygon(0 0,100% 0,100% 88%,50% 100%,0 88%)"}[d["cabecalho"]]
    card_css = {
        "branco": "background:#fff;border-radius:26px;box-shadow:0 14px 30px rgba(0,0,0,.18),0 2px 6px rgba(0,0,0,.08);",
        "sem_cartao": "background:transparent;",
        "vidro": "background:rgba(255,255,255,.78);border-radius:26px;box-shadow:0 10px 30px rgba(0,0,0,.15);"
                 "border:2px solid rgba(255,255,255,.9);",
        "borda": f"background:#fff;border-radius:18px;border:4px solid {p['primaria']};",
    }[d["cartao"]]
    price_bg = p["preco"]
    price_fg = _on(price_bg)
    price_css = {
        "etiqueta": f"background:{price_bg};color:{price_fg};border-radius:14px 40px 40px 14px;padding:4px 22px 4px 18px;"
                    f"box-shadow:0 6px 0 {_mix(price_bg, '#000000', .35)};",
        "selo": f"background:{p['secundaria']};color:{_on(p['secundaria'], '#fff', p['primaria'])};border-radius:999px;"
                f"padding:6px 26px;box-shadow:0 6px 0 {_mix(p['secundaria'], '#000000', .3)};",
        "bloco": f"background:{price_bg};color:{price_fg};border-radius:10px;padding:4px 18px;",
        "faixa": f"background:linear-gradient(90deg,{price_bg},{_mix(price_bg, p['secundaria'], .35)});color:{price_fg};"
                 f"border-radius:8px;padding:4px 20px;transform:skew(-8deg);",
    }[d["preco_estilo"]]
    deco = []
    if "estrelas" in d["decoracao"]:
        for x, y, s in ((6, 22, 46), (90, 30, 34), (84, 92, 52), (4, 88, 30)):
            deco.append(f'<div class="estrela" style="left:{x}%;top:{y}%;width:{s}px;height:{s}px"></div>')
    if "circulos" in d["decoracao"]:
        for x, y, s in ((-6, 40, 260), (88, 70, 320)):
            deco.append(f'<div class="circulo" style="left:{x}%;top:{y}%;width:{s}px;height:{s}px"></div>')
    if "confete" in d["decoracao"]:
        import random as _r  # noqa: PLC0415
        rnd = _r.Random(7)
        for _ in range(26):
            deco.append(f'<div class="confete" style="left:{rnd.uniform(0, 100):.1f}%;top:{rnd.uniform(18, 98):.1f}%;'
                        f'background:{rnd.choice([p["primaria"], p["secundaria"], "#ffffff"])};'
                        f'transform:rotate({rnd.uniform(0, 180):.0f}deg)"></div>')
    if comp == "diagonal":
        deco.append(f'<div class="faixa-deco" style="top:{int(H * .42)}px;height:{int(H * .3)}px;'
                    f'transform:rotate({d["angulo"]}deg)"></div>')
    if "faixas" in d["decoracao"]:
        deco.append(f'<div class="faixa-deco" style="top:{int(H * .58)}px"></div>')

    fit_js = """
    function fit(el, minPx){ let s=parseFloat(getComputedStyle(el).fontSize);
      // tolerância: acentos e descendentes passam um pouco da linha em qualquer tamanho
      while((el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+s*0.32)&&s>minPx){s-=1;el.style.fontSize=s+'px';} }
    document.fonts.ready.then(()=>{ document.querySelectorAll('.fit').forEach(e=>fit(e,12));
      document.querySelectorAll('.preco').forEach(e=>{ let s=parseFloat(getComputedStyle(e).fontSize);
        const box=e.parentElement; while(e.scrollWidth>box.clientWidth-8 && s>18){s-=2;e.style.fontSize=s+'px';} });
      document.body.setAttribute('data-ready','1'); });"""
    css = f"""
    {_font_faces()}
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{width:{W}px;height:{H}px;overflow:hidden;font-family:'Poppins',sans-serif;color:{p['texto']};background:{bg};position:relative}}
    .head{{position:absolute;left:0;top:0;width:100%;height:{head_h + 40}px;background:linear-gradient(135deg,{p['primaria']},
      {_mix(p['primaria'], '#000000', .25)});clip-path:{head_shape};z-index:2}}
    .head::after{{content:'';position:absolute;inset:0;background:repeating-linear-gradient(-45deg,rgba(255,255,255,.06) 0 18px,transparent 18px 36px)}}
    .loja{{position:absolute;left:{pad}px;top:{int(head_h * .14)}px;width:{int(W * .62)}px;height:{int(head_h * .42)}px;
      font-family:'{title_font}';font-size:{int(head_h * .36)}px;line-height:1.02;color:{on_primary};text-transform:uppercase;
      text-shadow:0 4px 0 rgba(0,0,0,.25);display:flex;align-items:center;z-index:3;letter-spacing:.5px;font-weight:900}}
    .titulo{{position:absolute;right:{pad}px;top:{int(head_h * .08)}px;width:{int(head_h * .86)}px;height:{int(head_h * .86)}px;
      background:{p['secundaria']};color:{_on(p['secundaria'], '#fff', p['primaria'])};z-index:4;display:flex;align-items:center;
      justify-content:center;text-align:center;font-family:'{title_font}';font-size:{int(head_h * .26)}px;text-transform:uppercase;
      line-height:.95;font-weight:900;clip-path:polygon(50% 0,61% 12%,75% 4%,78% 20%,94% 22%,88% 37%,100% 50%,88% 63%,94% 78%,78% 80%,75% 96%,
      61% 88%,50% 100%,39% 88%,25% 96%,22% 80%,6% 78%,12% 63%,0 50%,12% 37%,6% 22%,22% 20%,25% 4%,39% 12%);
      transform:rotate(-8deg);filter:drop-shadow(0 6px 0 rgba(0,0,0,.25));padding:{int(head_h * .2)}px}}
    .chamada{{position:absolute;left:{pad}px;top:{int(head_h * .62)}px;width:{int(W * .6)}px;height:{int(head_h * .18)}px;
      font-family:'PoppinsX';font-size:{int(head_h * .13)}px;color:{on_primary};opacity:.95;z-index:3;white-space:nowrap}}
    .grade{{position:absolute;left:{pad}px;top:{head_h + pad + 10}px;width:{area_w}px;height:{area_h}px;display:grid;
      grid-template-columns:repeat({cols},1fr);grid-template-rows:repeat({rows},1fr);gap:{gap}px;z-index:3}}
    .card{{{card_css}position:relative;display:flex;flex-direction:column;padding:{int(gap * .8)}px;min-width:0;min-height:0;overflow:visible}}
    .card.hero{{flex-direction:row;gap:{gap}px}}
    .livre{{position:absolute;left:0;top:0;width:100%;height:100%;z-index:3}}
    .u{{position:absolute;display:flex;flex-direction:column;min-width:0;min-height:0}}
    .u.card{{padding:calc(var(--s) * .05)}}
    .u.card .nome, .u.card .de, .u.card .un, .u.tipo .tipo-nome{{color:{_on('#ffffff') if d['cartao'] in ('branco', 'vidro', 'borda') else p['texto']}}}
    .u .nome{{font-size:calc(var(--s) * .072);height:2.45em}}
    .u .preco{{font-size:calc(var(--s) * .21)}}
    .u.card .info{{gap:2px}}
    .u .oferta{{font-size:calc(var(--s) * .05)}}
    .u .de{{font-size:calc(var(--s) * .05)}}
    .u .selo-desc{{width:calc(var(--s) * .2);height:calc(var(--s) * .2);font-size:calc(var(--s) * .065)}}
    .u .info{{flex:0 0 auto}}
    .u.card .foto{{flex:1 1 auto;min-height:calc(var(--s) * .5)}}
    .u.horiz{{flex-direction:row;gap:{gap}px;align-items:stretch}}
    .u.horiz .foto{{flex:1 1 52%}}
    .u.horiz .info{{flex:1 1 48%;justify-content:center}}
    .u.heroi .nome{{font-size:calc(var(--s) * .062)}}
    .u.heroi .preco{{font-size:calc(var(--s) * .185)}}
    .u.solta{{align-items:center}}
    .u .foto{{position:relative;min-height:0}}
    .u .foto img{{width:100%;height:100%;object-fit:contain;max-width:none;max-height:none}}
    .u.solta .foto{{flex:1 1 70%;position:relative;width:100%}}
    .u.solta .foto img{{filter:drop-shadow(0 22px 18px rgba(0,0,0,.35))}}
    .u.solta .chao{{position:absolute;left:12%;right:12%;bottom:-6px;height:16px;border-radius:50%;
      background:radial-gradient(rgba(0,0,0,.28),transparent 70%)}}
    .u.solta .nome{{font-size:calc(var(--s) * .085);height:2.4em;color:{p['texto']};margin-top:calc(var(--s) * .08)}}
    .u.solta .preco-sobre{{position:absolute;right:-4%;bottom:4%;width:62%;z-index:4;display:flex;flex-direction:column;
      align-items:flex-end;transform:rotate(-6deg)}}
    .u.solta .preco-sobre .precobox{{align-items:flex-end}}
    .u.solta .preco{{font-size:calc(var(--s) * .24)}}
    .u.tipo{{background:linear-gradient(120deg,#fff 0 58%,{_mix(p['primaria'], '#ffffff', .88)} 58%);border-radius:22px;
      box-shadow:0 10px 24px rgba(0,0,0,.14);flex-direction:row;align-items:center;padding:12px 18px 12px 26px;gap:14px;
      border-left:10px solid {p['primaria']}}}
    .tipo-nome{{flex:1 1 55%;height:100%;display:flex;align-items:center;font-family:'PoppinsX';
      font-size:calc(var(--s) * .3);line-height:1.08}}
    .tipo-preco{{flex:0 0 42%;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:0}}
    .u.tipo .preco{{font-size:calc(var(--s) * .5)}}
    .u.tipo .de{{font-size:calc(var(--s) * .14)}}
    .u.tipo .oferta{{font-size:calc(var(--s) * .13)}}
    .foto{{flex:1 1 55%;min-height:0;display:flex;align-items:center;justify-content:center}}
    .foto img{{max-width:100%;max-height:100%;object-fit:contain;filter:drop-shadow(0 12px 14px rgba(0,0,0,.28))}}
    .semfoto{{background:{_mix(p['primaria'], '#ffffff', .9)};border:3px dashed {_mix(p['primaria'], '#ffffff', .55)};
      border-radius:18px;padding:10%;color:{_mix(p['primaria'], '#000000', .25)};font-family:'PoppinsX';font-size:44px;
      text-align:center}}
    .semfoto span{{display:flex;align-items:center;justify-content:center;width:100%;height:100%}}
    .info{{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}}
    .card.hero .info{{flex:1 1 45%;justify-content:center}}
    .nome{{font-family:'Poppins';font-size:{46 if cols <= 2 else 32}px;line-height:1.15;text-align:center;width:100%;
      height:2.4em;overflow:hidden;display:flex;align-items:center;justify-content:center}}
    .titulo-txt{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;text-align:center}}
    .card.hero .nome{{font-size:54px}}
    .oferta{{background:{p['secundaria']};color:{_on(p['secundaria'], '#fff', p['texto'])};font-family:'PoppinsX';font-size:24px;
      padding:4px 14px;border-radius:999px;white-space:nowrap}}
    .precobox{{display:flex;flex-direction:column;align-items:center;width:100%}}
    .de{{font-family:'PoppinsS';font-size:24px;color:#6b6b72;text-decoration:line-through;text-decoration-color:#d71920;
      text-decoration-thickness:3px}}
    .preco{{{price_css}display:inline-flex;align-items:flex-start;font-family:'Anton';line-height:1;
      font-size:{110 if cols <= 2 else 84}px;white-space:nowrap}}
    .card.hero .preco{{font-size:150px}}
    .preco .rs{{font-family:'PoppinsX';font-size:.26em;margin:.55em .12em 0 0}}
    .preco .cts{{font-size:.45em;margin-top:.08em}}
    .preco.vazio{{font-family:'PoppinsS';font-size:28px;color:#888;background:none;box-shadow:none}}
    .un{{font-family:'PoppinsS';font-size:22px;color:#555}}
    .selo-desc{{position:absolute;right:-12px;top:-14px;width:96px;height:96px;background:{p['secundaria']};
      color:{_on(p['secundaria'], '#fff', p['primaria'])};z-index:5;display:flex;align-items:center;justify-content:center;
      font-family:'Anton';font-size:32px;border-radius:50%;transform:rotate(12deg);box-shadow:0 6px 0 rgba(0,0,0,.2)}}
    .rodape{{position:absolute;left:0;bottom:0;width:100%;height:{foot_h}px;background:{p['primaria']};color:{on_primary};
      display:flex;align-items:center;justify-content:center;font-family:'PoppinsX';font-size:{int(foot_h * .42)}px;z-index:3;
      letter-spacing:1px;text-transform:uppercase}}
    .estrela{{position:absolute;background:{p['secundaria']};z-index:1;opacity:.85;clip-path:polygon(50% 0,61% 35%,98% 35%,
      68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%)}}
    .circulo{{position:absolute;border-radius:50%;background:{_mix(p['primaria'], p['fundo'], .8)};z-index:1;opacity:.7}}
    .confete{{position:absolute;width:16px;height:7px;border-radius:2px;z-index:1;opacity:.8}}
    .faixa-deco{{position:absolute;left:-10%;width:120%;height:{int(H * .16)}px;background:{_mix(p['primaria'], p['fundo'], .78)};
      transform:rotate(-8deg);z-index:1}}
    """
    body = (f'<div class="head"></div><div class="loja fit">{loja or titulo}</div>'
            f'<div class="titulo"><div class="fit titulo-txt">{titulo}</div></div>'
            + (f'<div class="chamada fit">{chamada}</div>' if chamada else "")
            + "".join(deco) + (f'<div class="livre">{"".join(cards)}</div>' if free_units else
                               f'<div class="grade">{"".join(cards)}</div>')
            + (f'<div class="rodape">{validade}</div>' if validade else ""))
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{body}'
            f'<script>{fit_js}</script></body></html>')


def photo_data_uri(data):
    """Foto do produto pronta para o HTML: recorta o fundo branco (PNG com transparência) quando possível."""
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    from . import composer  # noqa: PLC0415

    img = Image.open(io.BytesIO(data))
    cut, _ = composer.cutout(img)
    cut.thumbnail((900, 900))
    buf = io.BytesIO()
    cut.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
