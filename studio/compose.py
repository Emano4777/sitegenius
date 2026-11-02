"""Composição determinística do encarte (Pillow).

A IA gera apenas o cenário de fundo. Fotos reais, nomes, preços, validade e
logo são desenhados aqui, exatamente como estão no briefing confirmado.
Usado pelo agente do Acer e pelos testes.
"""
import io
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .briefing import FORMATS

FONT_CANDIDATES = {
    True: [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ],
    False: [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ],
}
_font_cache = {}

PRICE_BG = (226, 27, 35)
PRICE_FG = (255, 255, 255)
ACCENT = (255, 204, 0)
INK = (24, 24, 28)
MUTED = (96, 96, 104)


def load_font(size, bold=False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    env = os.getenv("SG_FONT_BOLD" if bold else "SG_FONT_REGULAR")
    for path in ([env] if env else []) + FONT_CANDIDATES[bold]:
        if path and os.path.exists(path):
            font = ImageFont.truetype(path, size)
            break
    else:
        font = ImageFont.load_default(size=size)
    _font_cache[key] = font
    return font


def _text_width(draw, text, font):
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _fit_font(draw, text, max_width, start, minimum, bold=True):
    size = start
    while size > minimum:
        font = load_font(size, bold)
        if _text_width(draw, text, font) <= max_width:
            return font
        size -= 2
    return load_font(minimum, bold)


def _wrap(draw, text, font, max_width, max_lines):
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if _text_width(draw, trial, font) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _open(data, mode="RGBA"):
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert(mode)


def _background(size, scene_bytes):
    width, height = size
    if scene_bytes:
        scene = _open(scene_bytes, "RGB")
        return ImageOps.fit(scene, size, Image.LANCZOS)
    top, bottom = (255, 214, 0), (255, 120, 0)
    bg = Image.new("RGB", size)
    draw = ImageDraw.Draw(bg)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return bg


def _grid(count, area_w, area_h):
    """Escolhe colunas para deixar os cards o mais próximos de 4:5."""
    best = (0, 1, 1)
    for cols in range(1, 5):
        rows = math.ceil(count / cols)
        card_w, card_h = area_w / cols, area_h / rows
        score = min(card_w, card_h / 1.25)
        if score > best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def _shadowed_card(canvas, box, radius):
    x0, y0, x1, y1 = box
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 4, y0 + 8, x1 + 4, y1 + 8), radius, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(shadow)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, fill=(255, 255, 255, 255))


def _draw_price(canvas, draw, price, preco_de, unidade, box):
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if preco_de:
        font_de = load_font(max(12, int(height * 0.2)), False)
        text = f"de {preco_de}"
        tw = _text_width(draw, text, font_de)
        ty = y0 - int(height * 0.28)
        draw.text((x0 + (width - tw) / 2, ty), text, font=font_de, fill=MUTED)
        bbox = draw.textbbox((x0 + (width - tw) / 2, ty), text, font=font_de)
        mid = (bbox[1] + bbox[3]) / 2
        draw.line([(bbox[0], mid), (bbox[2], mid)], fill=MUTED, width=max(2, height // 30))
    draw.rounded_rectangle(box, radius=int(height * 0.25), fill=PRICE_BG)
    value = price.replace("R$", "").strip()
    whole, _, cents = value.partition(",")
    suffix = f"/{unidade}" if unidade else ""
    big = _fit_font(draw, whole, width * 0.55, int(height * 0.78), 14)
    small = load_font(max(10, int(big.size * 0.42)), True)
    parts = [("R$ ", small), (whole, big), ("," + cents + suffix, small)]
    total = sum(_text_width(draw, t, f) for t, f in parts)
    cursor = x0 + (width - total) / 2
    # Todos os trechos na mesma linha de base; altura de maiúscula ~72% do corpo.
    baseline = y0 + (height + big.size * 0.72) / 2
    for text, font in parts:
        draw.text((cursor, baseline), text, font=font, fill=PRICE_FG, anchor="ls")
        cursor += _text_width(draw, text, font)


def compose_flyer(briefing, scene_bytes=None, assets=None, logo_bytes=None, licenses=None):
    """Gera o encarte em JPEG. assets: {asset_id: bytes}. licenses: textos de atribuição."""
    assets = assets or {}
    fmt = FORMATS[briefing["formato"]]
    width, height = fmt["largura"], fmt["altura"]
    unit = width / 1080
    canvas = _background((width, height), scene_bytes).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    margin = int(36 * unit)

    # Cabeçalho: logo + loja + título.
    header_h = int(height * 0.15)
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (margin, margin, width - margin, margin + header_h), int(28 * unit), fill=(0, 0, 0, 150)
    )
    canvas.alpha_composite(panel)
    draw = ImageDraw.Draw(canvas)
    text_x = margin + int(28 * unit)
    if logo_bytes:
        logo = _open(logo_bytes)
        logo.thumbnail((int(header_h * 1.6), header_h - int(32 * unit)), Image.LANCZOS)
        ly = margin + (header_h - logo.height) // 2
        canvas.alpha_composite(logo, (text_x, ly))
        text_x += logo.width + int(24 * unit)
    text_w = width - margin - int(28 * unit) - text_x
    title = briefing.get("titulo") or "Ofertas"
    store = briefing.get("loja") or ""
    title_font = _fit_font(draw, title.upper(), text_w, int(header_h * 0.48), int(24 * unit))
    if store:
        store_font = _fit_font(draw, store, text_w, int(header_h * 0.24), int(16 * unit), bold=False)
        draw.text((text_x, margin + header_h * 0.3), store, font=store_font, fill=ACCENT, anchor="lm")
        draw.text((text_x, margin + header_h * 0.66), title.upper(), font=title_font, fill="white", anchor="lm")
    else:
        draw.text((text_x, margin + header_h / 2), title.upper(), font=title_font, fill="white", anchor="lm")

    # Rodapé: validade e atribuições exigidas pelas licenças das fotos.
    footer_lines = []
    if briefing.get("validade"):
        footer_lines.append(f"Ofertas válidas: {briefing['validade']}")
    if briefing.get("observacoes"):
        footer_lines.append(briefing["observacoes"])
    if licenses:
        footer_lines.append("Fotos: " + "; ".join(sorted(set(licenses)))[:160])
    footer_h = int((22 + 30 * len(footer_lines)) * unit) if footer_lines else 0
    if footer_lines:
        fy0 = height - margin - footer_h
        panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle((margin, fy0, width - margin, height - margin), int(18 * unit), fill=(0, 0, 0, 150))
        canvas.alpha_composite(panel)
        draw = ImageDraw.Draw(canvas)
        for i, line in enumerate(footer_lines):
            font = _fit_font(draw, line, width - 2 * margin - int(40 * unit), int(24 * unit if i == 0 else 18 * unit), 10, bold=(i == 0))
            draw.text((width / 2, fy0 + int((26 + 30 * i) * unit)), line, font=font, fill="white", anchor="mm")

    # Grade de produtos.
    products = briefing["produtos"]
    grid_top = margin + header_h + int(24 * unit)
    grid_bottom = height - margin - footer_h - (int(24 * unit) if footer_h else 0)
    area_w, area_h = width - 2 * margin, grid_bottom - grid_top
    cols, rows = _grid(len(products), area_w, area_h)
    gap = int(20 * unit)
    card_w = (area_w - gap * (cols - 1)) / cols
    card_h = min((area_h - gap * (rows - 1)) / rows, card_w * 1.45)
    used_h = rows * card_h + gap * (rows - 1)
    offset_y = grid_top + (area_h - used_h) / 2
    for index, product in enumerate(products):
        row, col = divmod(index, cols)
        items_in_row = min(cols, len(products) - row * cols)
        row_offset = (area_w - (items_in_row * card_w + gap * (items_in_row - 1))) / 2
        x0 = margin + row_offset + col * (card_w + gap)
        y0 = offset_y + row * (card_h + gap)
        box = tuple(int(v) for v in (x0, y0, x0 + card_w, y0 + card_h))
        _draw_card(canvas, product, assets.get(product.get("asset_id")), box, unit)

    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def _draw_card(canvas, product, photo_bytes, box, unit):
    x0, y0, x1, y1 = box
    card_w, card_h = x1 - x0, y1 - y0
    radius = int(min(card_w, card_h) * 0.08)
    _shadowed_card(canvas, box, radius)
    draw = ImageDraw.Draw(canvas)
    pad = int(card_w * 0.07)
    photo_h = int(card_h * 0.5)
    if photo_bytes:
        photo = _open(photo_bytes)
        photo.thumbnail((card_w - 2 * pad, photo_h - pad), Image.LANCZOS)
        px = x0 + (card_w - photo.width) // 2
        py = y0 + pad + (photo_h - pad - photo.height) // 2
        canvas.alpha_composite(photo, (px, py))
        draw = ImageDraw.Draw(canvas)
    name_font = load_font(max(12, int(card_h * 0.065)), True)
    lines = _wrap(draw, product["nome"], name_font, card_w - 2 * pad, 2)
    ny = y0 + photo_h + int(card_h * 0.03)
    for line in lines:
        draw.text((x0 + card_w / 2, ny), line, font=name_font, fill=INK, anchor="mt")
        ny += int(name_font.size * 1.15)
    price_h = int(card_h * 0.2)
    price_box = (x0 + pad, y1 - pad - price_h, x1 - pad, y1 - pad)
    _draw_price(canvas, draw, product["preco"], product.get("preco_de"), product.get("unidade"), price_box)
