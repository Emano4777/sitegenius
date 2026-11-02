"""Texto em superfícies (parede, placa, letreiro, camiseta…): perspectiva, curvatura e integração com a luz.

Só numpy + Pillow (leve: roda no servidor e no Acer). Fluxo:
1. A arte é gerada pedindo a superfície lisa e visível (creative.SURFACE_HINTS), sem letras.
2. Aqui a superfície é localizada pela textura: regiões lisas e contínuas, ligadas entre si.
   - Superfície plana (placa, letreiro, quadro, outdoor, fachada): a região mais "retangular" vira um quadrilátero
     (os 4 cantos extremos); o texto é projetado nele com transformação de perspectiva (homografia).
   - Parede: o maior retângulo liso dentro da região; se a região for um quadrilátero bem definido, usa perspectiva.
   - Camiseta/caneca: a peça é segmentada pela cor a partir do centro; o texto é curvado (cilindro + arco) e acompanha
     as dobras do tecido (deslocamento pelo relevo da luz).
3. Integração: a luz e a sombra da arte passam para as letras (multiplicação pela luminância local), a textura fina
   da superfície aparece por cima e, em parede, as letras ganham uma sombra curta oposta à luz.

Sem confiança na superfície, devolve None e o Studio usa o texto em destaque sobre a arte (sem inventar perspectiva).
"""
import math
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PLANAR = {"placa", "letreiro", "outdoor", "quadro", "tela", "fachada", "vitrine", "embalagem", "caixa", "rotulo", "rótulo"}
WALL = {"parede", "muro"}
CURVED = {"camiseta", "camisa", "bone", "boné", "caneca", "garrafa", "copo", "lata"}
WORK = 320  # lado maior da análise (rápido em CPU)


def kind_of(surface):
    s = (surface or "").lower()
    if s in WALL:
        return "parede"
    if s in CURVED:
        return "curva"
    if s in PLANAR:
        return "plana"
    return None


# ----------------------------------------------------------------------------------- análise

def _small(img):
    scale = WORK / max(img.size)
    w, h = max(16, int(img.width * scale)), max(16, int(img.height * scale))
    return img.convert("RGB").resize((w, h), Image.BILINEAR), scale


def _gradient(gray):
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    return np.hypot(gx, gy)


def _box_blur(arr, radius):
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.BoxBlur(radius)), dtype=np.float32)


def _components(mask, min_area):
    """Rótulos 4-conectados (BFS). Devolve lista de arrays de índices (ys, xs)."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    flat = mask.ravel()
    for start in np.flatnonzero(flat):
        if seen.flat[start]:
            continue
        sy, sx = divmod(int(start), w)
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        ys, xs = [], []
        while q:
            y, x = q.popleft()
            ys.append(y)
            xs.append(x)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(ys) >= min_area:
            comps.append((np.array(ys), np.array(xs)))
    return comps


def _quad_from_points(ys, xs):
    """Quadrilátero pelos 4 pontos extremos (TL, TR, BR, BL)."""
    s, d = xs + ys, xs - ys
    tl = (xs[np.argmin(s)], ys[np.argmin(s)])
    br = (xs[np.argmax(s)], ys[np.argmax(s)])
    tr = (xs[np.argmax(d)], ys[np.argmax(d)])
    bl = (xs[np.argmin(d)], ys[np.argmin(d)])
    return [tl, tr, br, bl]


def _poly_area(pts):
    area = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2


def _largest_rect(mask):
    """Maior retângulo alinhado aos eixos dentro da máscara (algoritmo do histograma)."""
    h, w = mask.shape
    heights = np.zeros(w, dtype=int)
    best = (0, 0, 0, 0, 0)
    for y in range(h):
        heights = np.where(mask[y], heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] >= cur:
                sx, sh = stack.pop()
                area = sh * (x - sx)
                if area > best[0]:
                    best = (area, sx, y - sh + 1, x, y + 1)
                start = sx
            stack.append((start, cur))
    return best


def _smooth_mask(small):
    gray = np.asarray(small.convert("L"), dtype=np.float32)
    grad = _box_blur(_gradient(gray), 2)
    thr = max(5.0, float(np.percentile(grad, 35)))
    return grad < thr, gray


def find_planar(img, prefer_wall=False):
    """Superfície plana lisa: {"quad": [(x,y)…] em pixels da imagem, "confianca", "rect"?}."""
    small, scale = _small(img)
    mask, gray = _smooth_mask(small)
    h, w = mask.shape
    total = h * w
    best = None
    for ys, xs in _components(mask, int(total * (0.06 if prefer_wall else 0.03))):
        area = len(ys)
        if area > total * 0.8:
            continue
        quad = _quad_from_points(ys, xs)
        qarea = _poly_area(quad)
        if qarea <= 0:
            continue
        fill = min(1.0, area / qarea)
        touches = (ys.min() == 0) + (ys.max() == h - 1) + (xs.min() == 0) + (xs.max() == w - 1)
        score = area * fill * (0.75 if touches >= 3 else 1.0)
        if best is None or score > best["score"]:
            best = {"score": score, "quad": quad, "fill": fill, "area": area, "ys": ys, "xs": xs}
    if best is None:
        return None
    inv = 1 / scale
    out = {"preenchimento": round(best["fill"], 2), "area": round(best["area"] / total, 3)}
    if best["fill"] >= 0.84:
        out["quad"] = [(float(x * inv), float(y * inv)) for x, y in best["quad"]]
        out["confianca"] = round(min(1.0, (best["fill"] - 0.6) / 0.35), 2)
        return out
    if prefer_wall:
        region = np.zeros_like(mask)
        region[best["ys"], best["xs"]] = True
        area, x0, y0, x1, y1 = _largest_rect(region)
        if area >= total * 0.05 and (x1 - x0) >= w * 0.25:
            x0, y0, x1, y1 = (float(v) * inv for v in (x0, y0, x1, y1))
            out["quad"] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            out["confianca"] = round(min(1.0, area / (total * 0.15)), 2)
            out["frontal"] = True
            return out
    return None


def find_garment(img):
    """Peça (camiseta/caneca) segmentada pela cor a partir do centro. Devolve a área do peito/estampa."""
    small, scale = _small(img)
    arr = np.asarray(small, dtype=np.float32)
    h, w, _ = arr.shape
    smooth, _ = _smooth_mask(small)
    seeds = [(int(h * fy), int(w * fx)) for fy in (0.42, 0.5, 0.36) for fx in (0.5, 0.45, 0.55)]
    seed = next(((y, x) for y, x in seeds if smooth[y, x]), seeds[0])
    ref = arr[max(0, seed[0] - 3):seed[0] + 4, max(0, seed[1] - 3):seed[1] + 4].reshape(-1, 3).mean(axis=0)
    dist = np.sqrt(((arr - ref) ** 2).sum(axis=2))
    region = dist < 38
    comps = [c for c in _components(region, int(h * w * 0.08)) if seed in zip(c[0].tolist(), c[1].tolist())]
    if not comps:
        return None
    ys, xs = comps[0]
    if len(ys) > h * w * 0.75:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    bw, bh = x1 - x0, y1 - y0
    cx = xs.mean()
    rect = (cx - bw * 0.24, y0 + bh * 0.2, cx + bw * 0.24, y0 + bh * 0.46)
    mask = np.zeros((h, w), dtype=bool)
    mask[ys, xs] = True
    rx0, ry0, rx1, ry1 = (int(v) for v in rect)
    inside = mask[max(0, ry0):ry1, max(0, rx0):rx1].mean() if ry1 > ry0 and rx1 > rx0 else 0
    if inside < 0.88:
        return None
    inv = 1 / scale
    return {"rect": tuple(float(v * inv) for v in rect), "cor": tuple(int(c) for c in ref),
            "confianca": round(float(inside), 2), "largura_peca": float(bw * inv)}


# ----------------------------------------------------------------------------------- texto e projeção

def _label(text_lines, font_loader, color, max_w, max_h):
    """Imagem RGBA do texto (até 2 linhas), no maior tamanho que cabe em max_w × max_h."""
    lo, hi, best = 8, int(max_h), None
    while lo <= hi:
        size = (lo + hi) // 2
        font = font_loader(size)
        widths = [font.getlength(line) for line in text_lines]
        height = size * 1.15 * len(text_lines)
        if max(widths) <= max_w and height <= max_h:
            best, lo = (size, font, widths), size + 1
        else:
            hi = size - 1
    if best is None:
        return None
    size, font, widths = best
    pad = int(size * 0.25)
    w, h = int(max(widths)) + 2 * pad, int(size * 1.15 * len(text_lines)) + 2 * pad
    alpha = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(alpha)
    for i, line in enumerate(text_lines):
        draw.text(((w - widths[i]) / 2, pad + i * size * 1.15), line, font=font, fill=255)
    label = Image.new("RGBA", (w, h), color + (0,))
    label.putalpha(alpha)
    return label


def _split_lines(text, font_loader, ratio):
    """1 ou 2 linhas, o que encaixar melhor na proporção da superfície."""
    words = text.split()
    if len(words) < 2:
        return [text]
    font = font_loader(40)
    one = font.getlength(text) / 46
    best, best_err = [text], abs(math.log(max(one, 0.01) / ratio))
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        r = max(font.getlength(a), font.getlength(b)) / (46 * 2)
        err = abs(math.log(max(r, 0.01) / ratio)) + 0.08
        if err < best_err:
            best, best_err = [a, b], err
    return best


def _homography(src, dst):
    """Coeficientes de Image.transform(PERSPECTIVE): mapeiam pontos de dst (saída) para src (rótulo)."""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = [c for pt in src for c in pt]
    return np.linalg.solve(np.array(a, dtype=np.float64), np.array(b, dtype=np.float64)).tolist()


def _inset(quad, fx, fy):
    tl, tr, br, bl = [np.array(p, dtype=float) for p in quad]

    def lerp(a, b, t):
        return a + (b - a) * t

    def at(u, v):
        return lerp(lerp(tl, tr, u), lerp(bl, br, u), v)

    return [tuple(at(fx, fy)), tuple(at(1 - fx, fy)), tuple(at(1 - fx, 1 - fy)), tuple(at(fx, 1 - fy))]


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _shade_and_texture(base, layer, region_box):
    """Passa luz/sombra e a textura fina da arte para as letras (integração com o ambiente)."""
    x0, y0, x1, y1 = (int(v) for v in region_box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(base.width, x1), min(base.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return layer
    gray = np.asarray(base.convert("L"), dtype=np.float32)
    blur = np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(max(2, min(base.size) * 0.02))), dtype=np.float32)
    mean = max(1.0, float(blur[y0:y1, x0:x1].mean()))
    shade = np.clip(blur / mean, 0.55, 1.35) ** 0.85
    detail = gray - np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
    rgba = np.asarray(layer, dtype=np.float32).copy()
    rgb = rgba[..., :3] * shade[..., None] + detail[..., None] * 0.6
    rgba[..., :3] = np.clip(rgb, 0, 255)
    out = Image.fromarray(rgba.astype(np.uint8), "RGBA")
    a = out.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    out.putalpha(a)
    return out


def _light_direction(base, box):
    x0, y0, x1, y1 = (int(v) for v in box)
    gray = np.asarray(base.convert("L"), dtype=np.float32)[max(0, y0):y1, max(0, x0):x1]
    if gray.size == 0:
        return 1, 1
    h, w = gray.shape
    dx = gray[:, w // 2:].mean() - gray[:, :w // 2].mean()
    dy = gray[h // 2:, :].mean() - gray[:h // 2, :].mean()
    # A sombra vai para o lado oposto ao mais claro.
    return (-1 if dx > 4 else (1 if dx < -4 else 1)), (-1 if dy > 4 else 1)


def _text_color(base, box, user_color=None, dark=(28, 28, 32), light=(248, 248, 246)):
    if user_color:
        return tuple(user_color)
    x0, y0, x1, y1 = (int(v) for v in box)
    gray = np.asarray(base.convert("L"), dtype=np.float32)[max(0, y0):y1, max(0, x0):x1]
    return dark if gray.size and gray.mean() > 135 else light


def apply_planar(base, quad, text, font_loader, user_color=None, wall=False):
    """Projeta o texto no quadrilátero (perspectiva) e integra com a luz. Devolve (imagem, info)."""
    inner = _inset(quad, 0.1, 0.18 if not wall else 0.28)
    top_w, bot_w = _dist(inner[0], inner[1]), _dist(inner[3], inner[2])
    left_h, right_h = _dist(inner[0], inner[3]), _dist(inner[1], inner[2])
    box = (min(p[0] for p in quad), min(p[1] for p in quad), max(p[0] for p in quad), max(p[1] for p in quad))
    color = _text_color(base, box, user_color)
    ratio = max(top_w, bot_w) / max(1.0, max(left_h, right_h))
    lines = _split_lines(text, font_loader, ratio)
    label = _label(lines, font_loader, color, max(top_w, bot_w) * 2, max(left_h, right_h) * 2)
    if label is None:
        return None
    # Mantém a proporção das letras: encaixa o rótulo no centro do quadrilátero interno.
    lw, lh = label.size
    qw, qh = (top_w + bot_w) / 2, (left_h + right_h) / 2
    k = min(qw / lw, qh / lh)
    fx = max(0.0, (1 - lw * k / qw) / 2)
    fy = max(0.0, (1 - lh * k / qh) / 2)
    target = _inset(inner, fx, fy)
    coeffs = _homography([(0, 0), (lw, 0), (lw, lh), (0, lh)], target)
    layer = label.transform(base.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    layer = _shade_and_texture(base, layer, box)
    out = base.convert("RGBA")
    if wall:
        sx, sy = _light_direction(base, box)
        off = max(2, int(min(base.size) * 0.005))
        shadow = layer.getchannel("A").filter(ImageFilter.GaussianBlur(off))
        shadow_img = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow_img.putalpha(shadow.point(lambda v: int(v * 0.45)))
        out.alpha_composite(shadow_img, (sx * off, sy * off))
    out.alpha_composite(layer)
    skew = abs(top_w - bot_w) / max(top_w, bot_w) + abs(left_h - right_h) / max(left_h, right_h)
    return out.convert("RGB"), {"modo": "perspectiva" if skew > 0.04 else "frontal", "linhas": lines,
                                "quad": [tuple(round(c) for c in p) for p in target]}


def _remap(src, map_x, map_y):
    """Amostragem bilinear (numpy) de src RGBA nas coordenadas map_x/map_y."""
    arr = np.asarray(src, dtype=np.float32)
    h, w = arr.shape[:2]
    x = np.clip(map_x, 0, w - 1.001)
    y = np.clip(map_y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    out = (arr[y0, x0] * (1 - fx) * (1 - fy) + arr[y0, x0 + 1] * fx * (1 - fy)
           + arr[y0 + 1, x0] * (1 - fx) * fy + arr[y0 + 1, x0 + 1] * fx * fy)
    valid = (map_x >= 0) & (map_x <= w - 1) & (map_y >= 0) & (map_y <= h - 1)
    out[~valid] = 0
    return out


def apply_curved(base, rect, text, font_loader, user_color=None, cylinder=0.55, bend=0.035):
    """Estampa curva: cilindro horizontal + arco leve + dobras do tecido. Devolve (imagem, info)."""
    x0, y0, x1, y1 = rect
    rw, rh = x1 - x0, y1 - y0
    color = _text_color(base, rect, user_color)
    lines = _split_lines(text, font_loader, rw / max(1, rh))
    label = _label(lines, font_loader, color, rw * 2, rh * 2)
    if label is None:
        return None
    lw, lh = label.size
    k = min(rw / lw, rh / lh)
    tw, th = lw * k, lh * k
    ox, oy = x0 + (rw - tw) / 2, y0 + (rh - th) / 2
    bx0, by0 = int(ox - tw * 0.05), int(oy - th * 0.3)
    bx1, by1 = int(ox + tw * 1.05), int(oy + th * 1.3)
    bx0, by0, bx1, by1 = max(0, bx0), max(0, by0), min(base.width, bx1), min(base.height, by1)
    yy, xx = np.mgrid[by0:by1, bx0:bx1].astype(np.float32)
    u = (xx - ox) / tw
    v = (yy - oy) / th
    # Cilindro: a parte central fica maior e as bordas comprimidas, como num tórax/caneca.
    t = np.clip(2 * u - 1, -1, 1)
    asin_k = math.asin(cylinder)
    u_src = 0.5 + np.arcsin(np.clip(t * cylinder, -0.999, 0.999)) / (2 * asin_k)
    v_src = v - bend * (1 - t ** 2) * 3
    gray = np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(max(2, rw * 0.02))), dtype=np.float32)
    gy, gx = np.gradient(gray[by0:by1, bx0:bx1])
    amp = th * 0.004
    map_x = u_src * lw + gx * amp * lw / tw
    map_y = v_src * lh + gy * amp * lh / th
    warped = _remap(label, map_x, map_y)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(Image.fromarray(np.clip(warped, 0, 255).astype(np.uint8), "RGBA"), (bx0, by0))
    layer = _shade_and_texture(base, layer, rect)
    out = base.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB"), {"modo": "curva", "linhas": lines, "rect": [round(c) for c in rect]}


def apply(base, surface, text, font_loader, user_color=None):
    """Aplica o texto na superfície pedida. Devolve (imagem, info) ou (None, motivo)."""
    kind = kind_of(surface)
    if kind is None:
        return None, "superfície não suportada"
    if kind == "curva":
        found = find_garment(base)
        if not found:
            return None, "peça não localizada com confiança"
        cyl = 0.75 if (surface or "").lower() in ("caneca", "garrafa", "copo", "lata") else 0.5
        result = apply_curved(base, found["rect"], text, font_loader, user_color, cylinder=cyl)
        if not result:
            return None, "texto não coube na peça"
        img, info = result
        info["confianca"] = found["confianca"]
        return img, info
    found = find_planar(base, prefer_wall=(kind == "parede"))
    if not found or found.get("confianca", 0) < 0.35:
        return None, "superfície lisa não localizada com confiança"
    result = apply_planar(base, found["quad"], text, font_loader, user_color, wall=(kind == "parede"))
    if not result:
        return None, "texto não coube na superfície"
    img, info = result
    info["confianca"] = found["confianca"]
    return img, info
