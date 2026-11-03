"""Compositor de encartes com liberdade de layout (referência visual ou composição criativa).

Um "design" descreve a lógica visual; os dados (produtos, preços, fotos, loja) vêm SEMPRE do briefing:
  design = {
    "fundo":      {"tipo": "solido|gradiente|diagonal|raios|listras|bolhas", "cores": [c1, c2]},
    "cabecalho":  {"altura": 0.2, "cor": c, "cor2": c, "forma": "reto|diagonal|onda", "logo_fundo": c|None,
                   "logo_cor": c, "selo": c|None, "selo_texto_cor": c},
    "tarja":      {"altura": 0.05, "cor": c, "texto_cor": c} | None,
    "conteudo":   {"fundo": c|None, "moldura": {"cor": c, "espessura": 0.01}|None},
    "arranjo":    {"tipo": "tabloide|grade|destaque|diagonal|zigue|assimetrica|livre", "angulo": graus},
    "produto":    {"cartao": c|None, "forma": "livre|circulo", "sombra": bool},
    "preco":      {"estilo": "numeros|selo|explosao|circulo|etiqueta", "cor": c, "selo_cor": c, "selo_texto": c,
                   "sobrescrito": bool, "posicao": "direita|abaixo|sobre", "escala": 0.4, "selo_fracao": 0.4},
    "nome":       {"cor": c},
    "seed": int,
  }
Garantias: todos os produtos do briefing aparecem uma vez, com o nome e o preço exatos; nenhum texto é cortado (as
fontes encolhem até caber); fotos nunca são redesenhadas (só recortadas do fundo branco quando possível).
"""
import io
import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import typography
from .briefing import FORMATS


# --------------------------------------------------------------------------------------------- utilidades de cor
def luma(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def contrast_on(bg, light=(255, 255, 255), dark=(22, 22, 26)):
    return dark if luma(bg) > 150 else light


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def tup(c, default=(0, 0, 0)):
    return tuple(int(v) for v in c[:3]) if c else default


# --------------------------------------------------------------------------------------------- texto
def font(family, weight, size):
    return typography.get_font(family, weight, size)


def fit(text, family, weight, max_w, max_h, minimum=10):
    size = int(max_h)
    while size > minimum:
        f = font(family, weight, size)
        bb = f.getbbox(text)
        if bb[2] - bb[0] <= max_w and bb[3] - bb[1] <= max_h:
            return f
        size -= max(1, size // 18)
    return font(family, weight, minimum)


def wrap_fit(text, family, weight, max_w, max_h, max_lines=3, minimum=10):
    """Maior fonte em que o texto cabe na caixa (quebrando em até max_lines linhas). Devolve (fonte, linhas)."""
    size = int(max_h)
    while size >= minimum:
        f = font(family, weight, size)
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if f.getlength(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(f.getlength(ln) <= max_w for ln in lines) and len(lines) * line_h(f) <= max_h:
            return f, lines
        size -= max(1, size // 16)
    f = font(family, weight, minimum)
    return f, [text]


def line_h(f):
    """Altura real de uma linha (parte visível: do topo das maiúsculas ao fim das descendentes)."""
    bb = f.getbbox("ÁgjpqyÇ")
    return (bb[3] - bb[1]) * 1.08


def draw_lines(draw, lines, f, box, color, align="center", shadow=None):
    x0, y0, x1, y1 = box
    lh = line_h(f)
    top_off = f.getbbox("Á")[1]
    y = y0 + (y1 - y0 - lh * len(lines)) / 2 - top_off
    for ln in lines:
        w = f.getlength(ln)
        x = x0 + (x1 - x0 - w) / 2 if align == "center" else (x1 - w if align == "right" else x0)
        if shadow:
            draw.text((x + 2, y + 2), ln, font=f, fill=shadow)
        draw.text((x, y), ln, font=f, fill=color)
        y += lh


# --------------------------------------------------------------------------------------------- formas
def burst(cx, cy, r_out, r_in, points=16, rot=0.0):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + math.pi * i / points
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def shadowed_paste(canvas, tile, pos, blur=10, offset=(6, 10), alpha=110):
    if tile.mode != "RGBA":
        tile = tile.convert("RGBA")
    sh = Image.new("RGBA", (tile.width + blur * 4, tile.height + blur * 4), (0, 0, 0, 0))
    a = tile.split()[3].point(lambda v: v * alpha // 255)
    sh.paste((0, 0, 0, 255), (blur * 2, blur * 2), a)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(sh, (int(pos[0] - blur * 2 + offset[0]), int(pos[1] - blur * 2 + offset[1])))
    canvas.alpha_composite(tile, (int(pos[0]), int(pos[1])))


# --------------------------------------------------------------------------------------------- fotos
def cutout(photo):
    """Recorta o produto do fundo claro e uniforme (foto de embalagem). Se o fundo não é uniforme, mantém a foto
    (e o design põe um cartão atrás). Devolve (imagem RGBA aparada, recortou: bool)."""
    img = ImageOps.exif_transpose(photo).convert("RGBA")
    if img.getchannel("A").getextrema()[0] < 250:  # já tem transparência
        bbox = img.getchannel("A").getbbox()
        out = img.crop(bbox) if bbox else img
        out.info["bbox"] = bbox or (0, 0, img.width, img.height)
        return out, True
    rgb = img.convert("RGB")
    small = rgb.copy()
    small.thumbnail((360, 360))
    w, h = small.size
    border = [small.getpixel((x, 0)) for x in range(w)] + [small.getpixel((x, h - 1)) for x in range(w)] + \
             [small.getpixel((0, y)) for y in range(h)] + [small.getpixel((w - 1, y)) for y in range(h)]
    light = [p for p in border if min(p) > 225 and max(p) - min(p) < 22]
    if len(light) < len(border) * 0.82:
        return img, False
    # Preenche a partir das bordas (só o fundo conectado à borda some; o branco da embalagem fica).
    mask = Image.new("L", small.size, 0)
    ref = small.convert("L")
    flood = small.copy()
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if min(flood.getpixel((x, y))) > 225:
            ImageDraw.floodfill(flood, (x, y), (255, 0, 255), thresh=28)
    diff = ImageChops.difference(flood, Image.new("RGB", small.size, (255, 0, 255))).convert("L")
    # Encolhe a máscara e suaviza a borda: sem o halo claro do fundo antigo em volta do recorte.
    mask = diff.point(lambda v: 255 if v > 8 else 0).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(1.4))
    del ref
    mask = mask.resize(img.size, Image.LANCZOS)
    out = img.copy()
    out.putalpha(mask)
    bbox = mask.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bbox or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < img.width * img.height * 0.08:
        return img, False
    piece = out.crop(bbox)
    piece.info["bbox"] = bbox
    return piece, True


def fit_photo(photo, max_w, max_h):
    ph = photo.copy()
    scale = min(max_w / ph.width, max_h / ph.height)
    return ph.resize((max(1, int(ph.width * scale)), max(1, int(ph.height * scale))), Image.LANCZOS)


# --------------------------------------------------------------------------------------------- preço
def split_price(price):
    value = (price or "").replace("R$", "").strip()
    whole, _, cents = value.partition(",")
    return whole, cents


def price_tile(price, price_de, unit, design, w, h, hero=False):
    """Bloco do preço (RGBA w x h): números grandes com centavos sobrescritos, selo, explosão ou etiqueta."""
    if not split_price(price)[0]:  # prévia com preço a definir: aviso claro, nunca um selo vazio
        tile = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        f = fit("preço a definir", "poppins", 700, w * 0.9, h * 0.3)
        ImageDraw.Draw(tile).text((w / 2, h / 2), "preço a definir", font=f, fill=(150, 150, 156), anchor="mm")
        return tile
    p = design["preco"]
    style = p.get("estilo", "numeros")
    tile = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    fg = tup(p.get("cor"), (20, 20, 20))
    pad = 0
    de_h = h * 0.2 if price_de else 0
    unit_h = h * 0.14 if unit else 0
    body = (0, de_h, w, h - unit_h)
    if style in ("selo", "explosao", "circulo", "etiqueta"):
        bg = tup(p.get("selo_cor"), (215, 25, 32))
        fg = tup(p.get("selo_texto"), contrast_on(bg))
        bx0, by0, bx1, by1 = body
        if style == "explosao":
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            r = min(bx1 - bx0, by1 - by0) / 2
            d.polygon(burst(cx, cy, r, r * 0.8, 18, 0.2), fill=bg)
            pad = r * 0.3
            body = (cx - r + pad, cy - r * 0.62, cx + r - pad, cy + r * 0.62)
        elif style == "circulo":
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            r = min(bx1 - bx0, by1 - by0) / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=bg)
            body = (cx - r * 0.75, cy - r * 0.5, cx + r * 0.75, cy + r * 0.5)
        elif style == "etiqueta":
            notch = (by1 - by0) * 0.45
            d.polygon([(bx0 + notch, by0), (bx1, by0), (bx1, by1), (bx0 + notch, by1), (bx0, (by0 + by1) / 2)], fill=bg)
            d.ellipse((bx0 + notch * 0.55, (by0 + by1) / 2 - notch * 0.12, bx0 + notch * 0.79, (by0 + by1) / 2 + notch * 0.12),
                      fill=(255, 255, 255, 230))
            body = (bx0 + notch, by0 + (by1 - by0) * 0.1, bx1 - (by1 - by0) * 0.12, by1 - (by1 - by0) * 0.1)
        else:
            d.rounded_rectangle(body, radius=int((by1 - by0) * 0.22), fill=bg)
            body = (bx0 + (by1 - by0) * 0.15, by0 + (by1 - by0) * 0.08, bx1 - (by1 - by0) * 0.15, by1 - (by1 - by0) * 0.08)
    _price_text(d, price, body, fg, p.get("sobrescrito", True))
    if price_de:
        f = fit(f"de {price_de}", "poppins", 600, w * 0.95, de_h * 0.8)
        txt = f"de {price_de}"
        tw = f.getlength(txt)
        x = (w - tw) / 2
        col = tup(design["nome"].get("cor"), (40, 40, 40))
        d.text((x, 0), txt, font=f, fill=col)
        bb = d.textbbox((x, 0), txt, font=f)
        d.line([(bb[0], (bb[1] + bb[3]) / 2), (bb[2], (bb[1] + bb[3]) / 2)], fill=(200, 20, 30), width=max(2, int(f.size / 9)))
    if unit:
        f = fit(unit, "poppins", 600, w * 0.95, unit_h * 0.85)
        col = tup(design["nome"].get("cor"), (40, 40, 40))
        d.text(((w - f.getlength(unit)) / 2, h - unit_h), unit, font=f, fill=col)
    return tile


def _price_text(d, price, box, color, superscript=True):
    """R$ pequeno + inteiro grande + centavos (sobrescritos ou na linha), centralizados na caixa."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    whole, cents = split_price(price)
    cents = ("," + cents) if cents and not superscript else cents
    size = int(bh * 1.25)
    while size > 10:
        big = font("anton", 400, size)
        small = font("anton", 400, int(size * (0.42 if superscript else 0.55)))
        rs = font("poppins", 800, int(size * 0.22))
        total = rs.getlength("R$") + size * 0.05 + big.getlength(whole) + small.getlength(cents) + size * 0.03
        bb = big.getbbox(whole)
        if total <= bw and (bb[3] - bb[1]) <= bh:
            break
        size -= max(1, size // 20)
    bb = big.getbbox("0")
    digit_h = bb[3] - bb[1]
    top = y0 + (bh - digit_h) / 2 - bb[1]
    x = x0 + (bw - total) / 2
    rb = rs.getbbox("R$")
    d.text((x, top + bb[1] + digit_h - (rb[3]) - 2), "R$", font=rs, fill=color)
    x += rs.getlength("R$") + size * 0.05
    d.text((x, top), whole, font=big, fill=color)
    x += big.getlength(whole) + size * 0.03
    if cents:
        sb = small.getbbox("0")
        cy = top + bb[1] - sb[1] if superscript else top + bb[1] + digit_h - (sb[3])
        d.text((x, cy), cents, font=small, fill=color)


# --------------------------------------------------------------------------------------------- fundo / cabeçalho
def background(size, design, rnd):
    W, H = size
    f = design["fundo"]
    c1, c2 = tup(f["cores"][0]), tup(f["cores"][1] if len(f["cores"]) > 1 else f["cores"][0])
    tipo = f.get("tipo", "solido")
    bg = Image.new("RGBA", size, c1 + (255,))
    d = ImageDraw.Draw(bg, "RGBA")
    if tipo == "gradiente":
        col = Image.new("RGB", (1, 256))
        for y in range(256):
            col.putpixel((0, y), mix(c1, c2, y / 255))
        bg = col.resize(size).convert("RGBA")
    elif tipo == "diagonal":
        ang = math.radians(design["arranjo"].get("angulo") or -14)
        cut = H * 0.55
        dy = math.tan(ang) * W
        d.polygon([(0, cut - dy / 2), (W, cut + dy / 2), (W, H), (0, H)], fill=c2 + (255,))
        d.polygon([(0, cut - dy / 2 - H * 0.02), (W, cut + dy / 2 - H * 0.02), (W, cut + dy / 2), (0, cut - dy / 2)],
                  fill=mix(c2, (255, 255, 255), 0.45) + (255,))
    elif tipo == "raios":
        cx, cy = W * rnd.uniform(0.3, 0.7), H * rnd.uniform(0.3, 0.5)
        n = 24
        for i in range(0, n, 2):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            R = max(W, H) * 1.5
            d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))],
                      fill=c2 + (70,))
    elif tipo == "listras":
        step = int(W * 0.08)
        for x in range(-H, W + H, step * 2):
            d.polygon([(x, 0), (x + step, 0), (x + step - H * 0.6, H), (x - H * 0.6, H)], fill=c2 + (40,))
    elif tipo == "bolhas":
        for _ in range(14):
            r = rnd.uniform(0.04, 0.16) * W
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            d.ellipse((x - r, y - r, x + r, y + r), fill=c2 + (45,))
    return bg


def header(canvas, design, briefing, box):
    """Cabeçalho: faixa de cor com a loja (em "logotipo") e, se o design pede, um selo de explosão com o título."""
    x0, y0, x1, y1 = box
    W = canvas.width
    hd = design["cabecalho"]
    d = ImageDraw.Draw(canvas, "RGBA")
    c = tup(hd.get("cor"), (215, 25, 32))
    c2 = tup(hd.get("cor2"), mix(c, (0, 0, 0), 0.25))
    forma = hd.get("forma", "reto")
    if forma == "diagonal":
        d.polygon([(x0, y0), (x1, y0), (x1, y1 - (y1 - y0) * 0.22), (x0, y1)], fill=c + (255,))
    elif forma == "onda":
        pts = [(x0, y0), (x1, y0)]
        for i in range(41):
            t = i / 40
            pts.append((x1 - t * (x1 - x0), y1 - (y1 - y0) * 0.1 + math.sin(t * math.pi * 2) * (y1 - y0) * 0.08))
        d.polygon(pts, fill=c + (255,))
    else:
        d.rectangle((x0, y0, x1, y1), fill=c + (255,))
        d.rectangle((x0, y1 - (y1 - y0) * 0.06, x1, y1), fill=c2 + (255,))
    h = y1 - y0
    loja = briefing.get("loja") or ""
    titulo = (briefing.get("titulo") or "Ofertas").strip()
    has_seal = bool(hd.get("selo"))
    logo_w = W * (0.6 if has_seal else 0.9)
    lx0 = x0 + W * 0.05
    if loja:
        lf, lines = wrap_fit(loja.upper(), "archivo", 400, logo_w - h * 0.2, h * 0.42, 2, 14)
        tw = max(lf.getlength(ln) for ln in lines)
        th = len(lines) * line_h(lf)
        bx0 = lx0 if has_seal else (W - tw) / 2 - h * 0.12
        pill = (bx0, y0 + h * 0.14, bx0 + tw + h * 0.24, y0 + h * 0.14 + th + h * 0.14)
        if hd.get("logo_fundo"):
            d.rounded_rectangle(pill, radius=int(h * 0.08), fill=tup(hd["logo_fundo"]) + (255,))
            logo_col = tup(hd.get("logo_cor"), c)
            if abs(luma(logo_col) - luma(tup(hd["logo_fundo"]))) < 60:
                logo_col = contrast_on(tup(hd["logo_fundo"]), (255, 255, 255), c)
        else:  # sem fundo: a loja precisa contrastar com a faixa
            logo_col = contrast_on(c)
        draw_lines(d, lines, lf, pill, logo_col)
        ty0 = pill[3] + h * 0.04
    else:
        ty0 = y0 + h * 0.15
    if has_seal:
        r = min(h * 0.55, W * 0.2)
        cx, cy = x1 - W * 0.05 - r, y0 + h * 0.52
        seal = tup(hd["selo"])
        d.polygon(burst(cx, cy, r, r * 0.82, 20, 0.15), fill=seal + (255,))
        f, lines = wrap_fit(titulo.upper(), "anton", 400, r * 1.25, r * 1.05, 3, 12)
        draw_lines(d, lines, f, (cx - r * 0.66, cy - r * 0.55, cx + r * 0.66, cy + r * 0.55),
                   tup(hd.get("selo_texto_cor"), contrast_on(seal)))
    else:
        bottom = y1 - h * (0.26 if forma in ("diagonal", "onda") else 0.1)
        if bottom - ty0 > h * 0.12:
            f = fit(titulo.upper(), "anton", 400, W * 0.8, (bottom - ty0) * 0.9)
            d.text((W / 2, (ty0 + bottom) / 2), titulo.upper(), font=f, fill=contrast_on(c), anchor="mm")


# --------------------------------------------------------------------------------------------- arranjos
def slots(n, area, design, hero_index, rnd):
    """Caixas (x0, y0, x1, y1, angulo, destaque) para n produtos dentro da área."""
    x0, y0, x1, y1 = area
    W, H = x1 - x0, y1 - y0
    tipo = design["arranjo"].get("tipo", "grade")
    gap = W * 0.025
    out = []
    if n == 0:
        return out
    if tipo in ("destaque", "tabloide", "assimetrica") and n >= 3 and hero_index is not None:
        if tipo == "assimetrica":
            hw = W * 0.56
            out.append((x0, y0, x0 + hw, y1, 0, True))
            rest = n - 1
            ch = (H - gap * (rest - 1)) / rest
            for i in range(rest):
                out.append((x0 + hw + gap, y0 + i * (ch + gap), x1, y0 + i * (ch + gap) + ch, 0, False))
        else:
            hh = H * (0.46 if n <= 4 else 0.36)
            out.append((x0, y0, x1, y0 + hh, 0, True))
            out += [(a, b, c, e, ang, False) for a, b, c, e, ang, _ in _grid(n - 1, (x0, y0 + hh + gap, x1, y1), gap)]
        return _reorder(out, hero_index)
    if tipo == "diagonal":
        ang = design["arranjo"].get("angulo") or -12
        cols = 2 if n > 2 else 1
        rows = math.ceil(n / cols)
        cw, ch = (W - gap) / cols if cols > 1 else W * 0.8, (H - gap * (rows - 1)) / rows
        for i in range(n):
            r, c = divmod(i, cols)
            shift = (r / max(1, rows - 1) - 0.5) * W * 0.12 if cols > 1 else (r / max(1, rows - 1) - 0.5) * W * 0.2
            bx = x0 + c * (cw + gap) + shift + (W - cw) / 2 * (cols == 1)
            if cols > 1 and r == rows - 1 and n % cols == 1:
                bx = x0 + (W - cw) / 2 + shift
            by = y0 + r * (ch + gap)
            out.append((bx, by, bx + cw, by + ch, ang, False))  # mesma inclinação: a diagonal fica evidente
        return out
    if tipo == "zigue":
        ch = (H - gap * (n - 1)) / n
        for i in range(n):
            by = y0 + i * (ch + gap)
            off = W * 0.08 * (1 if i % 2 else -1)
            out.append((x0 + max(0, off), by, x1 + min(0, off), by + ch, 0, False))
        return out
    if tipo == "livre":
        base = _grid(n, area, gap)
        return [(a + rnd.uniform(-1, 1) * gap, b + rnd.uniform(-1, 1) * gap, c + rnd.uniform(-1, 1) * gap,
                 e + rnd.uniform(-1, 1) * gap, rnd.uniform(-7, 7), False) for a, b, c, e, _, _ in base]
    return _grid(n, area, gap)


def _reorder(out, hero_index):
    return out  # o destaque é o primeiro slot; quem chama ordena os produtos (destaque primeiro)


def _tile_photo_area(cw, ch, aspect=0.8):
    side = _fit_area(cw * SIDE_SPLIT, ch * 0.9, aspect)
    stack = _fit_area(cw * 0.9, ch * (1 - STACK_PRICE - STACK_NAME) * 0.92, aspect)
    return max(side if cw * (1 - SIDE_SPLIT) > ch * 0.35 else 0, stack)


def _grid(n, area, gap):
    """Linhas x colunas que dão a MAIOR foto por produto (e sobra pouco espaço vazio)."""
    x0, y0, x1, y1 = area
    W, H = x1 - x0, y1 - y0
    best = None
    for cols in range(1, 6):
        rows = math.ceil(n / cols)
        cw, ch = (W - gap * (cols - 1)) / cols, (H - gap * (rows - 1)) / rows
        if cw < W * 0.18:
            continue
        empty = (cols * rows - n) / (cols * rows)
        score = _tile_photo_area(cw, ch) * (1 - 0.6 * empty)
        if best is None or score > best[0]:
            best = (score, cols, rows, cw, ch)
    _, cols, rows, cw, ch = best
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        ox = x0 + (W - (in_row * cw + gap * (in_row - 1))) / 2
        out.append((ox + c * (cw + gap), y0 + r * (ch + gap), ox + c * (cw + gap) + cw, y0 + r * (ch + gap) + ch, 0, False))
    return out


# --------------------------------------------------------------------------------------------- produto
SIDE_SPLIT = 0.56     # lado a lado: foto à esquerda até 56% da largura
STACK_PRICE = 0.30    # empilhado: faixa do preço
STACK_NAME = 0.14     # empilhado: faixa do nome


def _fit_area(box_w, box_h, aspect):
    if box_w <= 0 or box_h <= 0:
        return 0.0
    return min(box_w, box_h * aspect) * min(box_h, box_w / aspect)


def side_beats_stack(w, h, aspect, offer):
    """Foto ao lado (preço à direita) ou empilhada (preço embaixo): a que deixa a foto MAIOR, sem apertar o preço."""
    side = _fit_area(w * SIDE_SPLIT, h * 0.9, aspect)
    rest = h * (1 - STACK_PRICE - STACK_NAME - (0.09 if offer else 0)) * 0.92
    stack = _fit_area(w * 0.9, rest, aspect)
    return side > stack * 1.05 and w * (1 - SIDE_SPLIT) > h * 0.35


def discount_label(price, price_de):
    """“-33%” calculado dos preços reais (nunca inventado); sem preço antigo, “OFERTA”."""
    from .briefing import price_to_cents  # noqa: PLC0415

    now_c = price_to_cents((price or "").replace("R$", "").strip())
    old_c = price_to_cents((price_de or "").replace("R$", "").strip())
    if now_c and old_c and old_c > now_c:
        pct = int(round((old_c - now_c) * 100 / old_c))
        if 5 <= pct <= 90:
            return f"-{pct}%"
    return "OFERTA"


def sticker(tile, product, design, ph_box, size):
    d = ImageDraw.Draw(tile)
    r = size * 0.13
    cx, cy = ph_box[2] - r * 0.9, ph_box[1] + r * 0.95
    color = tup(design["preco"].get("selo_cor"), (215, 25, 32))
    if luma(color) > 170:
        color = (215, 25, 32)
    d.polygon(burst(cx, cy, r, r * 0.8, 16, 0.3), fill=color + (255,))
    text = discount_label(product.get("preco"), product.get("preco_de"))
    f = fit(text, "anton", 400, r * 1.3, r * 0.75)
    d.text((cx, cy), text, font=f, fill=contrast_on(color), anchor="mm")


def product_tile(product, photo, design, w, h, hero=False, badge=False):
    """Produto (RGBA w x h): foto recortada, nome e preço na posição do design (direita/abaixo/sobre)."""
    w, h = int(w), int(h)
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    pd = design["produto"]
    pos = design["preco"].get("posicao", "abaixo")
    card = pd.get("cartao")
    cut, was_cut = (cutout(photo) if photo is not None else (None, False))
    if card or (cut is not None and not was_cut and pd.get("forma") != "circulo"):
        cc = tup(card, (255, 255, 255))
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=int(min(w, h) * 0.06), fill=cc + (255,))
    name_col = tup(design["nome"].get("cor"), (30, 30, 34))
    if card:
        name_col = contrast_on(tup(card), (255, 255, 255), (30, 30, 34)) if luma(tup(card)) < 120 else name_col
    pad = min(w, h) * 0.05
    unit = product.get("unidade") or ""
    oferta = product.get("oferta") or ""
    escala = design["preco"].get("escala", 0.42) * (1.1 if hero else 1)
    off_h = 0
    if pos != "sobre":
        aspect = (cut.width / cut.height) if cut is not None else 1.0
        pos = "direita" if side_beats_stack(w, h, aspect, bool(oferta)) else "abaixo"
    if pos == "direita":
        split = SIDE_SPLIT
        ph_box = (pad, pad, w * split, h - pad)
        pr_h = min(h * 0.5, (w * (1 - split)) * 0.62)
        off_h = h * 0.12 if oferta else 0
        name_box = (w * split + pad * 0.5, pad, w - pad, h - pad - pr_h - off_h - h * 0.02)
        pr_box = (w * split - pad * 0.5, h - pad - pr_h, w - pad * 0.5, h - pad)
    elif pos == "sobre":
        ph_box = (pad, pad, w - pad, h * 0.78)
        pr_h = h * escala
        pr_box = (w * 0.38, h - pad - pr_h, w - pad, h - pad)
        name_box = (pad, h * 0.78, w * 0.4, h - pad)
    else:  # abaixo: foto, nome, (oferta), preço — cada um na sua faixa, sem sobreposição
        pr_h = min(h * STACK_PRICE, w * 0.42)
        name_h = h * STACK_NAME
        gap = h * 0.02
        ph_box = (pad, pad, w - pad, h - pad - pr_h - name_h - gap * 2 - (h * 0.09 if oferta else 0))
        name_box = (pad, ph_box[3] + gap, w - pad, ph_box[3] + gap + name_h)
        pr_box = (pad, h - pad - pr_h, w - pad, h - pad)
    # Foto
    if cut is not None:
        bw, bh = ph_box[2] - ph_box[0], ph_box[3] - ph_box[1]
        if pd.get("forma") == "circulo":
            r = int(min(bw, bh) / 2)
            circ = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
            ImageDraw.Draw(circ).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=(255, 255, 255, 255))
            inner = fit_photo(cut, r * 1.45, r * 1.45)
            circ.alpha_composite(inner, (r - inner.width // 2, r - inner.height // 2))
            px, py = int(ph_box[0] + (bw - 2 * r) / 2), int(ph_box[1] + (bh - 2 * r) / 2)
            shadowed_paste(tile, circ, (px, py), blur=6, offset=(3, 5), alpha=80)
        else:
            ph = fit_photo(cut, bw, bh)
            px, py = int(ph_box[0] + (bw - ph.width) / 2), int(ph_box[1] + (bh - ph.height) / 2)
            if was_cut and pd.get("sombra", True):
                shadowed_paste(tile, ph, (px, py), blur=8, offset=(4, 8), alpha=90)
            else:
                tile.alpha_composite(ph, (px, py))
    else:  # sem foto: painel suave com o nome no lugar da foto (nunca texto solto num espaço vazio)
        tint = tup(design["preco"].get("selo_cor"), (215, 25, 32))
        panel = (ph_box[0] + pad * 0.3, ph_box[1] + pad * 0.3, ph_box[2] - pad * 0.3, ph_box[3] - pad * 0.3)
        d.rounded_rectangle(panel, radius=int(min(w, h) * 0.08), fill=mix(tint, (255, 255, 255), 0.88) + (255,),
                            outline=mix(tint, (255, 255, 255), 0.55) + (255,), width=max(2, int(min(w, h) * 0.012)))
        f, lines = wrap_fit(product["nome"], "poppins", 800, (panel[2] - panel[0]) * 0.84, (panel[3] - panel[1]) * 0.55, 3)
        draw_lines(d, lines, f, panel, mix(tint, (0, 0, 0), 0.35))
    # Nome
    if name_box[3] - name_box[1] > 8 and (cut is not None):
        f, lines = wrap_fit(product["nome"], "poppins", 700, (name_box[2] - name_box[0]),
                            min(name_box[3] - name_box[1], h * 0.2), 3, 9)
        draw_lines(d, lines, f, name_box, name_col, align="center")
    # Oferta (ex.: "Leve 2 por R$ 45,00") acima do preço
    if oferta:
        oh = off_h or max(h * 0.08, (pr_box[3] - pr_box[1]) * 0.24)
        if not off_h:  # abaixo/sobre: a faixa fica logo acima do preço e o preço encolhe para dar espaço
            pr_box = (pr_box[0], pr_box[1] + oh + 4, pr_box[2], pr_box[3])
        ob = (pr_box[0], pr_box[1] - oh - 4, pr_box[2], pr_box[1] - 4)
        oc = tup(design["preco"].get("selo_cor"), (215, 25, 32))
        d.rounded_rectangle(ob, radius=int(oh * 0.3), fill=oc + (255,))
        f = fit(oferta, "poppins", 800, (ob[2] - ob[0]) * 0.92, oh * 0.72)
        d.text(((ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2), oferta, font=f, fill=contrast_on(oc), anchor="mm")
    # Preço
    pt = price_tile(product["preco"], product.get("preco_de"), unit, design, pr_box[2] - pr_box[0], pr_box[3] - pr_box[1], hero)
    tile.alpha_composite(pt, (int(pr_box[0]), int(pr_box[1])))
    if badge:  # selo de destaque no canto da foto; o preço mantém o mesmo tamanho dos outros (leitura uniforme)
        sticker(tile, product, design, ph_box, min(w, h))
    return tile


# --------------------------------------------------------------------------------------------- render
def render(briefing, assets, design, width=None):
    """Encarte (JPEG) com o design dado. Fotos/nomes/preços exatos do briefing."""
    fmt = FORMATS.get(briefing.get("formato"), FORMATS["feed_4x5"])
    W, H = fmt["largura"], fmt["altura"]
    if width:
        H, W = int(H * width / W), width
    rnd = random.Random(int(design.get("seed") or 1))
    canvas = background((W, H), design, rnd)
    m = int(W * 0.04)
    y = 0
    hd = design.get("cabecalho")
    if hd:
        hh = int(H * hd.get("altura", 0.18))
        header(canvas, design, briefing, (0, 0, W, hh))
        y = hh
    tj = design.get("tarja")
    if tj:
        th = int(H * tj.get("altura", 0.045))
        d = ImageDraw.Draw(canvas)
        col = tup(tj["cor"])
        d.rectangle((0, y, W, y + th), fill=col)
        titulo = (briefing.get("titulo") or "Ofertas").strip()
        text = tj.get("texto") or (f"Ofertas válidas {briefing['validade']}" if briefing.get("validade") else
                                   "Aproveite nossas ofertas" if hd and hd.get("selo") or titulo.lower() == "ofertas"
                                   else titulo)
        f = fit(text.upper(), "archivo", 400, W * 0.9, th * 0.62)
        d.text((W / 2, y + th / 2), text.upper(), font=f, fill=tup(tj.get("texto_cor"), contrast_on(col)), anchor="mm")
        y += th
    ct = design.get("conteudo") or {}
    foot_h = int(H * 0.045) if briefing.get("validade") and not tj else 0
    area = (m, y + m, W - m, H - m - foot_h)
    if ct.get("fundo") or ct.get("moldura"):
        d = ImageDraw.Draw(canvas)
        mo = ct.get("moldura")
        if mo:
            t = max(4, int(W * mo.get("espessura", 0.012)))
            d.rectangle((0, y, W, H), fill=tup(mo["cor"]))
            d.rectangle((t, y, W - t, H - t), fill=tup(ct.get("fundo"), (255, 255, 255)))
            area = (m + t, y + m, W - m - t, H - m - t - foot_h)
        else:
            d.rectangle((0, y, W, H), fill=tup(ct["fundo"]))
    if foot_h:
        d = ImageDraw.Draw(canvas)
        text = f"Ofertas válidas {briefing['validade']}"
        f = fit(text, "poppins", 600, W * 0.9, foot_h * 0.6)
        bgc = tup(ct.get("fundo") or design["fundo"]["cores"][-1])
        d.text((W / 2, H - m - foot_h / 2), text, font=f, fill=contrast_on(bgc), anchor="mm")
    if design["arranjo"].get("tipo") == "diagonal" and design["fundo"].get("tipo") in ("solido", None):
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        band = tup(design["preco"].get("selo_cor") or (design.get("cabecalho") or {}).get("cor"), (215, 25, 32))
        ax0, ay0, ax1, ay1 = area
        slope = math.tan(math.radians(design["arranjo"].get("angulo") or -12))
        mid, half = (ay0 + ay1) / 2, (ay1 - ay0) * 0.16
        d.polygon([(0, mid - half - slope * W / 2), (W, mid - half + slope * W / 2), (W, mid + half + slope * W / 2),
                   (0, mid + half - slope * W / 2)], fill=band + (46,))
        canvas.alpha_composite(layer)  # faixa translúcida (misturada, não pintada por cima)
    products = list(briefing.get("produtos") or [])
    hero_name = (briefing.get("layout") or {}).get("destaque")
    hero_idx = next((i for i, p in enumerate(products) if hero_name and p["nome"].lower() == str(hero_name).lower()), None)
    tipo = design["arranjo"].get("tipo")
    if hero_idx is None and tipo in ("destaque", "assimetrica") and len(products) >= 3:
        hero_idx = 0  # "tabloide" só tem destaque quando a referência ou o cliente pedem
    if hero_idx is not None:
        products.insert(0, products.pop(hero_idx))
        hero_idx = 0
    photos = {}
    for p in products:
        data = assets.get(p.get("asset_id"))
        if data:
            photos[p["nome"]] = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGBA")
    boxes = slots(len(products), area, design, hero_idx, rnd)
    frac = design["preco"].get("selo_fracao", 0) or 0
    n_badge = int(round(len(products) * frac)) if design["preco"].get("estilo") == "numeros" else 0
    for i, (p, (bx0, by0, bx1, by1, ang, hero)) in enumerate(zip(products, boxes)):
        tile = product_tile(p, photos.get(p["nome"]), design, bx1 - bx0, by1 - by0, hero=hero,
                            badge=(i < n_badge and (hero or i % 2 == 1 or n_badge >= len(products))))
        if ang:
            tile = tile.rotate(ang, expand=True, resample=Image.BICUBIC)
            # Gira sem sair da caixa: reduz se a versão girada ficou maior.
            s = min(1.0, (bx1 - bx0) / tile.width * 1.04, (by1 - by0) / tile.height * 1.04)
            if s < 1:
                tile = tile.resize((int(tile.width * s), int(tile.height * s)), Image.LANCZOS)
        px = (bx0 + bx1) / 2 - tile.width / 2
        py = (by0 + by1) / 2 - tile.height / 2
        canvas.alpha_composite(tile, (int(px), int(py)))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=91, optimize=True)
    return out.getvalue()


# --------------------------------------------------------------------------------------------- referência -> design
def from_reference(spec, briefing=None, seed=1):
    """Especificação da referência (refstyle.analyze + leitura visual) -> design do compositor.

    A organização (cabeçalho, tarja, moldura, fundo, fotos recortadas, preço solto/selo, posição do preço, centavos)
    segue a referência; a quantidade de produtos é a do briefing (adapta linhas/colunas e o destaque)."""
    hd = spec.get("cabecalho") or None
    preco = spec.get("preco") or {}
    content_bg = spec.get("fundo_conteudo") or [255, 255, 255]
    paleta = spec.get("paleta") or []
    visual = spec.get("visual") or {}
    accent = (hd or {}).get("principal") or next((c for c in paleta if max(c) - min(c) > 90), [215, 25, 32])
    design = {
        "seed": seed,
        "fundo": {"tipo": "solido", "cores": [content_bg, content_bg]},
        "cabecalho": None,
        "tarja": None,
        "conteudo": {"fundo": content_bg, "moldura": spec.get("moldura")},
        "arranjo": {"tipo": "tabloide" if visual.get("hierarquia") == "um_destaque" else "grade", "angulo": 0},
        "produto": {"cartao": None if spec.get("produtos", {}).get("recortados", True) else [255, 255, 255],
                    "forma": "livre", "sombra": True},
        "preco": {"estilo": "numeros" if preco.get("selo_fracao", 0) < 0.6 else
                  {"explosao": "explosao", "circulo": "circulo", "retangulo": "selo", "arredondado": "selo"}.get(
                      preco.get("selo_forma"), "selo"),
                  "cor": preco.get("cor") or [20, 20, 20], "selo_cor": preco.get("selo_cor") or accent,
                  "sobrescrito": preco.get("centavos_sobrescritos", True),
                  "posicao": preco.get("posicao") if preco.get("posicao") in ("direita", "abaixo", "sobre") else "direita",
                  "escala": max(0.3, min(0.6, (preco.get("altura_rel") or 0.4))), "selo_fracao": preco.get("selo_fracao", 0)},
        "nome": {"cor": [40, 40, 44] if luma(content_bg) > 140 else [245, 245, 245]},
    }
    # Selo do preço amarelo/claro sobre fundo claro some: usa a cor de destaque da referência.
    if design["preco"]["selo_cor"] and luma(tup(design["preco"]["selo_cor"])) > 170:
        design["preco"]["selo_cor"] = accent
        design["preco"]["selo_texto"] = [255, 255, 255]
    if hd:
        logo = spec.get("logo") or {}
        cores = hd.get("cores") or [accent]
        second = next((c for c in cores if _far(c, accent)), mix(tup(accent), (0, 0, 0), 0.3))
        lf = logo.get("cor_fundo")
        design["cabecalho"] = {
            "altura": max(0.12, min(0.3, hd.get("altura", 0.2) * 0.8)), "cor": accent, "cor2": second, "forma": "reto",
            "logo_fundo": [255, 255, 255] if not lf or _far(lf, accent) is False else lf,
            "logo_cor": accent if not lf or not _far(lf, accent) else logo.get("cor_texto") or [255, 255, 255],
            "selo": hd.get("selo"), "selo_texto_cor": [200, 20, 30] if hd.get("selo") else None,
        }
    tj = spec.get("tarja")
    if tj:
        design["tarja"] = {"altura": max(0.03, min(0.07, tj["fim"] - tj["inicio"])), "cor": tj["cor"],
                           "texto_cor": tj.get("cor_texto") or [255, 255, 255]}
    org = visual.get("organizacao")
    if org in ("diagonal", "zigue", "assimetrica", "grade"):
        design["arranjo"]["tipo"] = org
    return design


def _far(a, b):
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b)) > 160


# --------------------------------------------------------------------------------------------- composições criativas
ARRANJOS = ["tabloide", "diagonal", "assimetrica", "zigue", "livre", "grade"]
FUNDOS = ["diagonal", "raios", "listras", "bolhas", "gradiente"]
PRECOS = ["numeros", "explosao", "etiqueta", "selo", "circulo"]


def creative(palette, seed=1, prefs=None):
    """Composição criativa (sem referência): combina arranjo, fundo, preço e cabeçalho conforme o pedido do cliente
    (prefs: {"arranjo", "fundo", "preco", "cabecalho", "produto_forma", "cartao"}) e varia o resto pela semente."""
    rnd = random.Random(seed)
    prefs = prefs or {}
    c1 = tup(palette[0]) if palette else (215, 25, 32)
    c2 = tup(palette[1]) if len(palette) > 1 else (255, 204, 0)
    light = mix(c2, (255, 255, 255), 0.82) if luma(c2) < 200 else c2
    arr = prefs.get("arranjo") or rnd.choice(ARRANJOS)
    fundo = prefs.get("fundo") or rnd.choice(FUNDOS)
    estilo = prefs.get("preco") or rnd.choice(PRECOS)
    return {
        "seed": seed,
        "fundo": {"tipo": fundo, "cores": [light, c1 if fundo in ("diagonal",) else c2]},
        "cabecalho": {"altura": 0.17, "cor": c1, "cor2": mix(c1, (0, 0, 0), 0.3),
                      "forma": prefs.get("cabecalho") or rnd.choice(["reto", "diagonal", "onda"]),
                      "logo_fundo": [255, 255, 255] if rnd.random() < 0.6 else None,
                      "logo_cor": list(c1), "selo": list(c2) if rnd.random() < 0.7 else None,
                      "selo_texto_cor": list(contrast_on(c2, (255, 255, 255), c1))},
        "tarja": None,
        "conteudo": {},
        "arranjo": {"tipo": arr, "angulo": prefs.get("angulo") or rnd.choice([-12, -9, 8, 11])},
        "produto": {"cartao": prefs.get("cartao", [255, 255, 255] if arr in ("grade", "livre", "diagonal") else None),
                    "forma": prefs.get("produto_forma") or ("circulo" if rnd.random() < 0.2 else "livre"), "sombra": True},
        "preco": {"estilo": estilo, "cor": [20, 20, 22] if estilo == "numeros" else [255, 255, 255],
                  "selo_cor": list(c1), "sobrescrito": True, "posicao": rnd.choice(["abaixo", "direita"]) if arr in
                  ("zigue", "tabloide", "assimetrica") else "abaixo", "escala": 0.42, "selo_fracao": 0.35},
        "nome": {"cor": [30, 30, 34]},
    }
