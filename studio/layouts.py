"""Layouts de encarte: a IA sugere, o cliente escolhe e ajusta pela conversa.

Uma especificação de layout (dentro do briefing, em "layout") descreve o estilo e as escolhas do cliente:
  {"modelo": "jornal|farmacia|premium|relampago|catalogo", "cores": [rgb, ...], "destaque": "nome do produto",
   "fundo_ia": bool, "instrucoes": "texto livre do cliente"}
O renderizador transforma briefing + especificação em imagem, de forma determinística: fotos, nomes e preços vêm do
briefing (nunca redesenhados por IA). A IA de imagem (Q3) só entra, se pedido, para o fundo (scene_bytes).
"""
import io
import math

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import typography
from .briefing import FORMATS

STYLES = {
    "jornal": {"nome": "Jornal de ofertas", "fundo": [(255, 214, 0), (255, 170, 0)], "faixa": (215, 25, 32),
               "titulo_cor": (255, 255, 255), "cartao": (255, 255, 255), "preco_bg": (215, 25, 32),
               "preco_fg": (255, 255, 255), "texto": (30, 30, 34), "fonte_titulo": ("anton", 400),
               "fonte_nome": ("poppins", 700), "raio": 6, "borda": (215, 25, 32)},
    "farmacia": {"nome": "Farmácia", "fundo": [(232, 246, 252), (196, 228, 244)], "faixa": (0, 120, 190),
                 "titulo_cor": (255, 255, 255), "cartao": (255, 255, 255), "preco_bg": (0, 166, 81),
                 "preco_fg": (255, 255, 255), "texto": (20, 50, 80), "fonte_titulo": ("montserrat", 800),
                 "fonte_nome": ("montserrat", 600), "raio": 26, "borda": None},
    "premium": {"nome": "Premium escuro", "fundo": [(28, 28, 32), (8, 8, 10)], "faixa": None,
                "titulo_cor": (214, 180, 96), "cartao": (40, 40, 46), "preco_bg": (214, 180, 96),
                "preco_fg": (20, 20, 22), "texto": (240, 236, 228), "fonte_titulo": ("playfair", 800),
                "fonte_nome": ("montserrat", 500), "raio": 18, "borda": (214, 180, 96)},
    "relampago": {"nome": "Oferta relâmpago", "fundo": [(220, 20, 30), (150, 0, 10)], "faixa": (255, 214, 0),
                  "titulo_cor": (20, 20, 22), "cartao": (255, 255, 255), "preco_bg": (255, 214, 0),
                  "preco_fg": (180, 0, 10), "texto": (25, 25, 28), "fonte_titulo": ("anton", 400),
                  "fonte_nome": ("poppins", 800), "raio": 12, "borda": None, "listras": True},
    "catalogo": {"nome": "Catálogo moderno", "fundo": [(244, 244, 246), (226, 228, 232)], "faixa": None,
                 "titulo_cor": (24, 24, 28), "cartao": (255, 255, 255), "preco_bg": (24, 24, 28),
                 "preco_fg": (255, 255, 255), "texto": (24, 24, 28), "fonte_titulo": ("montserrat", 800),
                 "fonte_nome": ("montserrat", 500), "raio": 30, "borda": None},
}
ORDER = ["jornal", "farmacia", "premium", "relampago", "catalogo"]


def style_for(layout):
    base = dict(STYLES.get((layout or {}).get("modelo"), STYLES["jornal"]))
    cores = (layout or {}).get("cores") or []
    if cores:
        base["fundo"] = [tuple(cores[0]), tuple(cores[1] if len(cores) > 1 else cores[0])]
        if len(cores) > 1:
            base["preco_bg"] = tuple(cores[1]) if cores[1] != cores[0] else base["preco_bg"]
    return base


def _gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img.resize((w, h))


def _luma(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _font(spec, size):
    return typography.get_font(spec[0], spec[1], size)


def _fit(text, spec, max_w, start, minimum=12):
    size = start
    while size > minimum and _font(spec, size).getlength(text) > max_w:
        size -= 2
    return _font(spec, size)


def _wrap(text, font, max_w, lines=2):
    words, out, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        while font.getlength(out[-1] + "…") > max_w and len(out[-1]) > 1:
            out[-1] = out[-1][:-1]
        out[-1] += "…"
    return out


def _price_badge(canvas, style, price, price_de, box, hero=False):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    draw = ImageDraw.Draw(canvas)
    if price_de:
        f = _font(("poppins", 500), max(12, int(h * 0.26)))
        txt = f"de {price_de}"
        tw = f.getlength(txt)
        ty = y0 - h * 0.34
        col = style["texto"]
        draw.text((x0 + (w - tw) / 2, ty), txt, font=f, fill=col)
        bb = draw.textbbox((x0 + (w - tw) / 2, ty), txt, font=f)
        draw.line([(bb[0], (bb[1] + bb[3]) / 2), (bb[2], (bb[1] + bb[3]) / 2)], fill=col, width=max(2, h // 22))
    draw.rounded_rectangle(box, radius=int(h * (0.5 if style.get("raio", 0) > 20 else 0.18)), fill=style["preco_bg"])
    value = price.replace("R$", "").strip()
    whole, _, cents = value.partition(",")
    big = _fit(whole, ("poppins", 800), w * 0.6, int(h * 0.8), 14)
    small = _font(("poppins", 800), max(10, int(big.size * 0.42)))
    parts = [("R$ ", small), (whole, big), (("," + cents) if cents else "", small)]
    total = sum(f.getlength(t) for t, f in parts)
    cx = x0 + (w - total) / 2
    base = y0 + (h + big.size * 0.72) / 2
    for t, f in parts:
        draw.text((cx, base), t, font=f, fill=style["preco_fg"], anchor="ls")
        cx += f.getlength(t)


def _card(canvas, style, product, photo, box, hero=False):
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = x1 - x0, y1 - y0
    shadow = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 4, y0 + 8, x1 + 4, y1 + 8), style["raio"], fill=90)
    canvas.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x0, y0, x1, y1), style["raio"], fill=style["cartao"],
                           outline=style.get("borda"), width=3 if style.get("borda") else 0)
    pad = int(min(w, h) * 0.06)
    if hero:  # destaque: foto à esquerda, nome e preço à direita
        photo_box = (x0 + pad, y0 + pad, x0 + int(w * 0.5), y1 - pad)
        text_x0 = x0 + int(w * 0.52)
        if photo:
            ph = photo.copy()
            ph.thumbnail((photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]), Image.LANCZOS)
            canvas.paste(ph, (photo_box[0] + (photo_box[2] - photo_box[0] - ph.width) // 2,
                              photo_box[1] + (photo_box[3] - photo_box[1] - ph.height) // 2), ph if ph.mode == "RGBA" else None)
        tw = x1 - pad - text_x0
        f = _fit(product["nome"], style["fonte_nome"], tw, int(h * 0.13), 14)
        lines = _wrap(product["nome"], f, tw, 3)
        ty = y0 + int(h * 0.16)
        for line in lines:
            draw.text((text_x0, ty), line, font=f, fill=style["texto"])
            ty += int(f.size * 1.2)
        ph_h = int(h * 0.28)
        _price_badge(canvas, style, product["preco"], product.get("preco_de"),
                     (text_x0, y1 - pad - ph_h, x1 - pad, y1 - pad), hero=True)
        return
    photo_h = int(h * 0.5)
    if not photo:  # sem foto: o nome ocupa o lugar da foto, em destaque (nunca um espaço vazio)
        f = _fit(product["nome"], style["fonte_nome"], w - 2 * pad, int(h * 0.12), 12)
        lines = _wrap(product["nome"], f, w - 2 * pad, 3)
        ty = y0 + pad + (photo_h - pad - len(lines) * f.size * 1.2) / 2
        for line in lines:
            draw.text((x0 + (w - f.getlength(line)) / 2, ty), line, font=f, fill=style["texto"])
            ty += f.size * 1.2
        _price_badge(canvas, style, product["preco"], product.get("preco_de"),
                     (x0 + pad, y1 - pad - int(h * 0.19), x1 - pad, y1 - pad))
        return
    if photo:
        ph = photo.copy()
        ph.thumbnail((w - 2 * pad, photo_h - pad), Image.LANCZOS)
        canvas.paste(ph, (x0 + (w - ph.width) // 2, y0 + pad + (photo_h - pad - ph.height) // 2),
                     ph if ph.mode == "RGBA" else None)
    f = _font(style["fonte_nome"], max(12, int(h * 0.07)))
    ny = y0 + photo_h + int(h * 0.03)
    for line in _wrap(product["nome"], f, w - 2 * pad, 2):
        lw = f.getlength(line)
        draw.text((x0 + (w - lw) / 2, ny), line, font=f, fill=style["texto"])
        ny += int(f.size * 1.18)
    ph_h = int(h * 0.19)
    _price_badge(canvas, style, product["preco"], product.get("preco_de"), (x0 + pad, y1 - pad - ph_h, x1 - pad, y1 - pad))


def _background(size, style, scene_bytes=None):
    if scene_bytes:
        scene = ImageOps.fit(Image.open(io.BytesIO(scene_bytes)).convert("RGB"), size, Image.LANCZOS)
        return scene
    bg = _gradient(size, *style["fundo"])
    if style.get("listras"):
        draw = ImageDraw.Draw(bg, "RGBA")
        w, h = size
        step = int(w * 0.09)
        for x in range(-h, w + h, step * 2):
            draw.polygon([(x, 0), (x + step, 0), (x + step - h, h), (x - h, h)], fill=(255, 255, 255, 18))
    return bg


DEFAULT_PALETTE = [[215, 25, 32], [255, 204, 0]]


def design_for(briefing):
    """Design do compositor para layouts livres: referência visual analisada ou composição criativa (com semente).
    None = modelo clássico (STYLES)."""
    from . import composer  # noqa: PLC0415

    layout = briefing.get("layout") or {}
    modelo = layout.get("modelo") or ""
    ref = layout.get("referencia") or {}
    prefs = dict(layout.get("composicao") or {})
    seed = int(layout.get("semente") or 1)
    cores = layout.get("cores") or []
    if modelo == "referencia" and ref.get("spec"):
        design = composer.from_reference(ref["spec"], briefing, seed=seed)
        if cores:  # identidade do cliente: troca a cor de destaque mantendo a organização da referência
            accent = cores[0]
            if design.get("cabecalho"):
                design["cabecalho"].update({"cor": accent, "logo_cor": accent})
                if len(cores) > 1:
                    design["cabecalho"]["selo"] = cores[1]
            if design.get("tarja"):
                design["tarja"]["cor"] = accent
            design["preco"]["selo_cor"] = accent
    elif modelo.startswith("criativo") or (prefs and modelo not in STYLES):
        palette = cores or ((ref.get("spec") or {}).get("paleta") and
                            [c for c in ref["spec"]["paleta"] if max(c) - min(c) > 80][:2]) or DEFAULT_PALETTE
        if len(palette) < 2:
            palette = list(palette) + [DEFAULT_PALETTE[1]]
        design = composer.creative(palette, seed=seed, prefs=prefs)
    else:
        return None
    for key in ("arranjo", "angulo"):
        if prefs.get(key) is not None:
            design["arranjo"]["tipo" if key == "arranjo" else "angulo"] = prefs[key]
    if prefs.get("preco"):
        design["preco"]["estilo"] = prefs["preco"]
    if prefs.get("fundo"):
        design["fundo"]["tipo"] = prefs["fundo"]
    if prefs.get("produto_forma"):
        design["produto"]["forma"] = prefs["produto_forma"]
    if prefs.get("cabecalho") and design.get("cabecalho"):
        design["cabecalho"]["forma"] = prefs["cabecalho"]
    return design


def render(briefing, assets, scene_bytes=None, width=None):
    """Encarte final (JPEG) a partir do briefing (produtos/preços/fotos reais) e do layout escolhido."""
    design = design_for(briefing)
    if design is not None:
        from . import composer  # noqa: PLC0415

        return composer.render(briefing, assets, design, width=width)
    fmt = FORMATS.get(briefing.get("formato"), FORMATS["feed_4x5"])
    W, H = fmt["largura"], fmt["altura"]
    if width:
        H, W = int(H * width / W), width
    layout = briefing.get("layout") or {}
    style = style_for(layout)
    canvas = _background((W, H), style, scene_bytes)
    draw = ImageDraw.Draw(canvas)
    unit = W / 1080
    m = int(36 * unit)
    # Cabeçalho: loja + título.
    head_h = int(H * 0.14)
    if style.get("faixa"):
        draw.rectangle((0, 0, W, head_h + m), fill=style["faixa"])
    title = (briefing.get("titulo") or "Ofertas").upper() if style["fonte_titulo"][0] in ("anton",) else (briefing.get("titulo") or "Ofertas")
    tf = _fit(title, style["fonte_titulo"], W - 2 * m, int(head_h * 0.62), 20)
    loja = briefing.get("loja") or ""
    if loja:
        lf = _fit(loja, ("montserrat", 700), W - 2 * m, int(head_h * 0.26), 14)
        draw.text((W / 2, m + head_h * 0.2), loja, font=lf, fill=style["titulo_cor"], anchor="mm")
        draw.text((W / 2, m + head_h * 0.68), title, font=tf, fill=style["titulo_cor"], anchor="mm")
    else:
        draw.text((W / 2, m + head_h / 2), title, font=tf, fill=style["titulo_cor"], anchor="mm")
    # Rodapé: validade/observações (só o que o cliente informou).
    foot = " · ".join(x for x in (f"Válido {briefing['validade']}" if briefing.get("validade") else "",
                                  briefing.get("observacoes") or "") if x)
    foot_h = int(H * 0.05) if foot else 0
    if foot:
        ff = _fit(foot, ("poppins", 500), W - 2 * m, int(foot_h * 0.5), 10)
        col = (255, 255, 255) if _luma(style["fundo"][1]) < 140 else (30, 30, 34)
        draw.text((W / 2, H - m - foot_h / 2), foot, font=ff, fill=col, anchor="mm")
    # Produtos: destaque (se houver) ocupa a largura toda no topo; os demais em grade.
    products = list(briefing.get("produtos") or [])
    hero_name = layout.get("destaque")
    hero = next((p for p in products if hero_name and p["nome"].lower() == str(hero_name).lower()), None)
    rest = [p for p in products if p is not hero]
    top = m + head_h + int(28 * unit)
    bottom = H - m - foot_h - (int(20 * unit) if foot else 0)
    gap = int(22 * unit)
    photos = {}
    for p in products:
        data = assets.get(p.get("asset_id"))
        if data:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGBA")
            photos[p["nome"]] = img
    if hero:
        hero_h = int((bottom - top) * (0.42 if rest else 1.0))
        _card(canvas, style, hero, photos.get(hero["nome"]), (m, top, W - m, top + hero_h), hero=True)
        top += hero_h + gap
    if rest:
        area_w, area_h = W - 2 * m, bottom - top
        best = (0, 1, 1)
        for cols in range(1, 5):
            rows = math.ceil(len(rest) / cols)
            cw, ch = (area_w - gap * (cols - 1)) / cols, (area_h - gap * (rows - 1)) / rows
            score = min(cw, ch / 1.25)
            if score > best[0]:
                best = (score, cols, rows)
        _, cols, rows = best
        cw = (area_w - gap * (cols - 1)) / cols
        ch = min((area_h - gap * (rows - 1)) / rows, cw * 1.5)
        oy = top + (area_h - (rows * ch + gap * (rows - 1))) / 2
        for i, p in enumerate(rest):
            r, c = divmod(i, cols)
            in_row = min(cols, len(rest) - r * cols)
            ox = m + (area_w - (in_row * cw + gap * (in_row - 1))) / 2
            x0 = ox + c * (cw + gap)
            y0 = oy + r * (ch + gap)
            _card(canvas, style, p, photos.get(p["nome"]), (x0, y0, x0 + cw, y0 + ch))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def previews(briefing, assets, modelos=None, width=420):
    """Prévias pequenas dos estilos (mesmos produtos/preços/fotos), para o cliente escolher pela conversa."""
    modelos = modelos or ORDER[:3]
    out = []
    for modelo in modelos:
        lay = {**(briefing.get("layout") or {}), "modelo": modelo}
        if modelo.startswith("criativo:"):
            lay.update({"modelo": "criativo", "semente": int(modelo.split(":")[1])})
        elif modelo in STYLES:
            lay.update({"cores": [], "composicao": {}})
        out.append((modelo, render({**briefing, "layout": lay}, assets, width=width)))
    return out


def preview_name(modelo):
    if modelo == "referencia":
        return "Inspirado na sua referência"
    if modelo.startswith("criativo"):
        return "Composição criativa"
    return STYLES.get(modelo, {}).get("nome", modelo)
