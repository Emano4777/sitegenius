"""Compositor Pango/Cairo: desenha a MESMA composição (composicao.json do nosso pipeline) com Pango (forma das letras,
acentos, espaçamento) e Cairo (contornos vetoriais, gradientes, sombras com desfoque, profundidade, brilho).
Uso: venv_cairo/python render_cairo.py composicao.json saida.png"""
import json
import math
import os
import re
import sys
import tempfile
import time

import cairo
import manimpango
import numpy as np
from PIL import Image, ImageFilter

_REG = set()


def hexrgb(h, a=1.0):
    h = (h or "#ffffff").lstrip("#")
    h = (h + "000000")[:6] if len(h) != 6 else h
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a


_CACHE = {}


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _svg_lines(lines):
    """UMA chamada ao Pango para várias linhas (cada uma com a sua família): no Windows cada chamada custa ~2 s.
    lines: [(texto, família, negrito)]. Devolve uma lista de glifos por linha (na mesma ordem)."""
    fd, path = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    markup = "\n".join(f"<span font_family='{fam.replace(chr(39), '')}'{' weight=\"bold\"' if bold else ''}>{_esc(t)}</span>"
                       for t, fam, bold in lines)
    width = max(400, max(90 * len(t) for t, _, _ in lines) + 200)
    manimpango.MarkupUtils.text2svg(text=markup, font=lines[0][1].replace("'", ""), slant="NORMAL", weight="NORMAL", size=100, _=0,
                                    disable_liga=False, file_name=path, START_X=0, START_Y=150, width=width,
                                    height=260 * len(lines) + 300, justify=False, indent=0, line_spacing=-1, alignment=None,
                                    pango_width=None)
    svg = open(path, encoding="utf-8").read()
    os.remove(path)
    defs = dict(re.findall(r'<g id="(glyph-[\d-]+)">\s*<path d="([^"]*)"', svg))
    uses = [(g, float(x), float(y)) for g, x, y in re.findall(r'<use xlink:href="#(glyph-[\d-]+)" x="([-\d.e]+)" y="([-\d.e]+)"', svg)]
    rows = {}
    for g, x, y in uses:  # cada linha tem a sua linha de base (y)
        key = round(y, 1)
        rows.setdefault(key, []).append((defs.get(g, ""), x, y))
    ordered = [rows[k] for k in sorted(rows)]
    out = []
    for gl in ordered:
        out.append([(d, x, y) for d, x, y in gl if d.strip()])
    return out if len(out) == len(lines) else None


def prefetch(lines):
    """Uma chamada ao Pango por linha ainda não vista (juntar linhas numa chamada só embaralha a ordem delas)."""
    for c in dict.fromkeys(l for l in lines if l[0].strip() and l not in _CACHE):
        _CACHE[c] = (_svg_lines([c]) or [[]])[0]


def _line(text, font_file, family, weight):
    key = (text, family, (weight or 400) >= 650)
    if key not in _CACHE:
        prefetch([key])
    return _CACHE.get(key, [])


def _bbox(gl):
    xs, ys = [], []
    for d, x, y in gl:
        nums = [float(v) for v in re.findall(r"[-\d.e]+", d)]
        xs += [x + v for v in nums[0::2]]  # no tamanho 100 os glifos já vêm em pixels
        ys += [y + v for v in nums[1::2]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 1, 1)


def _family(font_file, family):
    """Nome da família como está DENTRO do arquivo (o CSS pode usar um apelido); registra a fonte no Pango."""
    if not font_file:
        return family
    if font_file not in _REG:
        manimpango.register_font(font_file)
        _REG.add(font_file)
    from PIL import ImageFont  # noqa: PLC0415
    return ImageFont.truetype(font_file, 20).getname()[0]


def _candidates(text):
    words = text.split()
    return [text] + ([" ".join(words[:k]) + "\n" + " ".join(words[k:]) for k in range(1, len(words))]
                     if len(words) > 1 and "\n" not in text else [])


def glyphs(text, font_file, family, weight=None):
    """Contornos das letras pelo Pango (tamanho 100), linha a linha, empilhadas e centralizadas. [(d, x, y)], bbox."""
    if font_file and font_file not in _REG:
        manimpango.register_font(font_file)
        _REG.add(font_file)
    family = _family(font_file, family)
    out, y_off = [], 0.0
    for ln in text.split("\n"):
        gl = _line(ln, font_file, family, weight)
        if not gl:
            continue
        x0, y0, x1, y1 = _bbox(gl)
        out += [(d, x - x0 - (x1 - x0) / 2, y - y0 + y_off) for d, x, y in gl]
        y_off += (y1 - y0) * 1.18
    return (out, _bbox(out)) if out else ([], (0, 0, 1, 1))


def path_into(ctx, gl, sx, ox, oy):
    for d, x, y in gl:
        toks = re.findall(r"[MLCZ]|[-\d.e]+", d)
        i = 0
        cmd = None
        while i < len(toks):
            t = toks[i]
            if t in "MLCZ":
                cmd = t
                i += 1
                if cmd == "Z":
                    ctx.close_path()
                continue
            n = 6 if cmd == "C" else 2
            v = [float(z) for z in toks[i:i + n]]
            i += n
            pts = [(ox + (x + v[k]) * sx, oy + (y + v[k + 1]) * sx) for k in range(0, n, 2)]
            if cmd == "M":
                ctx.move_to(*pts[0])
            elif cmd == "L":
                ctx.line_to(*pts[0])
            elif cmd == "C":
                ctx.curve_to(*pts[0], *pts[1], *pts[2])


def layer(W, H):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    return s, cairo.Context(s)


def to_pil(surf):
    W, H = surf.get_width(), surf.get_height()
    a = np.ndarray((H, W, 4), np.uint8, buffer=surf.get_data()).copy()
    return Image.fromarray(a[:, :, [2, 1, 0, 3]], "RGBA")


def shadow_of(img, dx, dy, blur, rgba):
    """Sombra/brilho só na área da camada (rápido: o desfoque não percorre a peça inteira)."""
    W, H = img.size
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bb = img.getchannel("A").getbbox()
    if not bb:
        return out
    pad = int(blur * 3 + abs(dx) + abs(dy) + 4)
    x0, y0, x1, y1 = max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad)
    a = img.getchannel("A").crop((x0, y0, x1, y1))
    if blur > 0.5:
        a = a.filter(ImageFilter.GaussianBlur(blur))
    col = Image.new("RGBA", a.size, tuple(int(c * 255) for c in rgba[:3]) + (0,))
    col.putalpha(a.point(lambda v: int(v * rgba[3])))
    out.paste(col, (x0 + int(dx), y0 + int(dy)), col)
    return out


def draw_text(canvas, el, W, H):
    x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
    text = el["texto"] or ""
    if not text.strip():
        return
    best = None
    words = text.split()
    cands = [text] if el.get("linha_unica") else [text] + ([" ".join(words[:k]) + "\n" + " ".join(words[k:]) for k in range(1, len(words))] if len(words) > 1 and "\n" not in text else [])
    for cand in cands:  # uma ou duas linhas: a que deixa a letra maior dentro da caixa
        gl, (x0, y0, x1, y1) = glyphs(cand, el.get("fonte"), el.get("familia"), el.get("peso"))
        if not gl:
            continue
        s = min(w * 0.96 / max(1, x1 - x0), h * 0.9 / max(1, y1 - y0))
        if not best or s > best[0]:
            best = (s, gl, (x0, y0, x1, y1))
    if not best:
        return
    s, gl, (x0, y0, x1, y1) = best
    ox = (x - x0 * s) if el.get("alinhar") == "esquerda" else x + (w - (x1 - x0) * s) / 2 - x0 * s
    oy = y + (h - (y1 - y0) * s) / 2 - y0 * s
    fx = el.get("efeito") or {"preenchimento": {"tipo": "solido", "cores": [el.get("cor", "#fff")]}, "contornos": [], "sombras": []}
    size = (y1 - y0) * s / max(1, text.count("\n") + 1)
    surf, ctx = layer(W, H)
    if fx.get("inclinacao") or fx.get("rotacao"):
        cx, cy = x + w / 2, y + h / 2
        ctx.translate(cx, cy)
        ctx.rotate(math.radians(fx.get("rotacao") or 0))
        ctx.transform(cairo.Matrix(1, 0, math.tan(math.radians(-(fx.get("inclinacao") or 0))), 1, 0, 0))
        ctx.translate(-cx, -cy)
    path_into(ctx, gl, s, ox, oy)
    path = ctx.copy_path()
    # contornos de fora para dentro (o mais largo primeiro)
    for o in sorted(fx.get("contornos") or [], key=lambda o: -o["espessura"]):
        ctx.new_path(); ctx.append_path(path)
        ctx.set_source_rgba(*hexrgb(o["cor"]))
        ctx.set_line_width(max(1.0, o["espessura"] * size * 2))
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke()
    fill = fx["preenchimento"]
    ctx.new_path(); ctx.append_path(path)
    cores = fill.get("cores") or [el.get("cor", "#fff")]
    if fill.get("tipo") in ("gradiente", "metalico"):
        g = cairo.LinearGradient(0, y, 0, y + h)
        stops = cores if fill["tipo"] == "gradiente" else ["#ffffff", cores[0], "#3a2a08", cores[0], "#ffffff"]
        for i, c in enumerate(stops):
            g.add_color_stop_rgba(i / max(1, len(stops) - 1), *hexrgb(c))
        ctx.set_source(g)
    else:
        ctx.set_source_rgba(*hexrgb(cores[0]))
    ctx.fill()
    txt = to_pil(surf)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    p = fx.get("profundidade")
    if p and p.get("camadas"):  # extrusão: cópias deslocadas na direção pedida
        step = max(1.0, size * 0.012)
        dx, dy = math.cos(math.radians(p["direcao"])) * step, math.sin(math.radians(p["direcao"])) * step
        mask = txt.getchannel("A")
        solid = Image.new("RGBA", (W, H), tuple(int(c * 255) for c in hexrgb(p["cor"])[:3]) + (255,))
        for k in range(p["camadas"], 0, -1):
            out.paste(solid, (int(dx * k), int(dy * k)), mask)
    for sh in fx.get("sombras") or []:
        out.alpha_composite(shadow_of(txt, sh["x"] * size, sh["y"] * size, sh["desfoque"] * size, hexrgb(sh["cor"], sh["opacidade"])))
    b = fx.get("brilho")
    if b:
        out.alpha_composite(shadow_of(txt, 0, 0, size * 0.12 * b["intensidade"], hexrgb(b["cor"], 0.9)))
    if el.get("halo"):
        dark = sum(hexrgb(cores[0])[:3]) > 1.5
        out.alpha_composite(shadow_of(txt, 0, 0, size * 0.25, (0.04, 0.04, 0.05, 0.75) if dark else (1, 1, 1, 0.6)))
    out.alpha_composite(txt)
    canvas.alpha_composite(out)


def draw_simple(canvas, el, W, H, color, shadow=True):
    e = {**el, "efeito": {"preenchimento": {"tipo": "solido", "cores": [color]}, "contornos": [],
                          "sombras": [{"x": 0, "y": 0.06, "desfoque": 0.12, "cor": "#000000", "opacidade": 0.55}] if shadow else []}}
    draw_text(canvas, e, W, H)


def rounded(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0); ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi); ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def draw_price(canvas, el, W, H):
    x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
    surf, ctx = layer(W, H)
    st = el["estilo"]
    if st == "explosao" or st == "circulo":
        cx, cy, r = x + w / 2, y + h / 2, min(w, h) * 0.5  # o retângulo medido já é a forma inteira
        if st == "circulo":
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
        else:
            for k in range(32):
                ang = k * math.pi / 16
                rr = r if k % 2 == 0 else r * 0.8
                (ctx.move_to if k == 0 else ctx.line_to)(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
            ctx.close_path()
    else:
        rounded(ctx, x, y, w, h, min(w, h) * (0.18 if st != "faixa" else 0.05))
    ctx.set_source_rgba(*hexrgb(el["bg"]))
    ctx.fill()
    bg = to_pil(surf)
    canvas.alpha_composite(shadow_of(bg, 0, h * 0.06, h * 0.08, (0, 0, 0, 0.45)))
    canvas.alpha_composite(bg)
    # R$ pequeno, inteiro grande, centavos no alto (mesma leitura do nosso compositor)
    bx, by_, bw, bh = el["b"]
    if el.get("de"):  # preço antigo no alto, dentro da forma; o preço novo logo abaixo
        draw_simple(canvas, {**el, "texto": "de " + el["de"], "b": [bx + bw * 0.2, by_ + bh * 0.1, bw * 0.6, bh * 0.18]}, W, H, el["fg"], shadow=False)
        by_, bh = by_ + bh * 0.26, bh * 0.66
    if st in ("explosao", "circulo"):  # texto dentro do círculo inscrito (secundário: aproveita mais a forma, como no nosso)
        inner = 0.68 if el.get("principal") else 0.84
        bx, bw = bx + bw * (1 - inner) / 2, bw * inner
    parts = [("R$", 0.30, 0.42), (el["inteiro"], 1.0, 0.0), ((el["centavos"] or "") + (" " + el["sufixo"] if el["sufixo"] else ""), 0.40, 0.0)]
    runs = []
    for txt, rel, drop in parts:
        if not txt.strip():
            continue
        gl, bb = glyphs(txt, el.get("fonte"), el.get("familia"))
        runs.append((gl, bb, rel, drop))
    if runs:
        base_h = max(r[1][3] - r[1][1] for r in runs if r[2] == 1.0) if any(r[2] == 1.0 for r in runs) else 100
        total_w = sum((r[1][2] - r[1][0]) * r[2] for r in runs) + base_h * 0.08 * (len(runs) - 1)
        fill = (0.86, 0.8) if el.get("principal") else (0.94, 0.9)  # preço secundário pequeno: ocupa mais a caixa
        s_ = min(bw * W / 100 * fill[0] / max(1, total_w), bh * H / 100 * fill[1] / max(1, base_h))
        cx = bx * W / 100 + (bw * W / 100 - total_w * s_) / 2
        top = by_ * H / 100 + (bh * H / 100 - base_h * s_) / 2
        surf, ctx = layer(W, H)
        for gl, bb, rel, drop in runs:
            ss = s_ * rel
            ox = cx - bb[0] * ss
            oy = top + base_h * s_ * drop - bb[1] * ss
            path_into(ctx, gl, ss, ox, oy)
            cx += (bb[2] - bb[0]) * ss + base_h * s_ * 0.08
        ctx.set_source_rgba(*hexrgb(el["fg"]))
        ctx.fill()
        canvas.alpha_composite(to_pil(surf))



def draw_product(canvas, el, W, H):
    x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
    im = Image.open(el["foto"]).convert("RGBA")
    f = min(w / im.width, h / im.height)
    im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
    px, py = int(x + (w - im.width) / 2), int(y + h - im.height)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if el.get("cartao"):
        card = Image.new("RGBA", (im.width + 16, im.height + 16), (255, 255, 255, 255))
        card.paste(im.convert("RGB"), (8, 8))
        lay.paste(card, (px - 8, py - 8))
    else:
        lay.paste(im, (px, py), im)
    canvas.alpha_composite(shadow_of(lay, 0, h * 0.03, h * 0.05, (0, 0, 0, 0.5)))
    canvas.alpha_composite(lay)


def draw_list(canvas, el, W, H):
    x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
    surf, ctx = layer(W, H)
    rounded(ctx, x, y, w, h, 22)
    ctx.set_source_rgba(1, 1, 1, 0.93)
    ctx.fill()
    panel = to_pil(surf)
    canvas.alpha_composite(shadow_of(panel, 0, 14, 26, (0, 0, 0, 0.35)))
    canvas.alpha_composite(panel)
    n = max(1, len(el["linhas"]))
    rh = el["b"][3] / n
    for i, (name, price) in enumerate(el["linhas"]):
        ry = el["b"][1] + i * rh
        # mesmo desenho do nosso compositor: nome numa linha, à esquerda; preço num selo colorido à direita
        draw_simple(canvas, {**el, "texto": name, "linha_unica": True, "alinhar": "esquerda",
                             "b": [el["b"][0] + el["b"][2] * 0.04, ry + rh * 0.25, el["b"][2] * 0.6, rh * 0.5]}, W, H, "#1e1e22", shadow=False)
        bx, by_, bw, bh = el["b"][0] + el["b"][2] * 0.72, ry + rh * 0.16, el["b"][2] * 0.24, rh * 0.68
        surf, ctx = layer(W, H)
        rounded(ctx, bx * W / 100, by_ * H / 100, bw * W / 100, bh * H / 100, min(bw * W / 100, bh * H / 100) * 0.25)
        ctx.set_source_rgba(*hexrgb(el["bg"]))
        ctx.fill()
        canvas.alpha_composite(to_pil(surf))
        draw_simple(canvas, {**el, "texto": price, "fonte": el["fonte_preco"], "familia": el["familia_preco"], "linha_unica": True,
                             "b": [bx + bw * 0.06, by_ + bh * 0.1, bw * 0.88, bh * 0.8]}, W, H, el["fg"], shadow=False)


def draw_seal(canvas, el, W, H):
    x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
    if el.get("so_texto"):  # a forma já está na base: só o texto do selo
        draw_simple(canvas, {**el, "b": [el["b"][0] + el["b"][2] * 0.15, el["b"][1] + el["b"][3] * 0.25, el["b"][2] * 0.7,
                                         el["b"][3] * 0.5]}, W, H, el["cor2"], shadow=False)
        return
    surf, ctx = layer(W, H)
    cx, cy = x + w / 2, y + h / 2
    ctx.translate(cx, cy); ctx.rotate(math.radians(el.get("giro", -8))); ctx.translate(-cx, -cy)
    if el["forma"] in ("circulo", "carimbo", "estrela"):
        r = min(w, h) / 2
        if el["forma"] == "estrela":
            for k in range(32):
                rr = r if k % 2 == 0 else r * 0.84
                (ctx.move_to if k == 0 else ctx.line_to)(cx + rr * math.cos(k * math.pi / 16), cy + rr * math.sin(k * math.pi / 16))
            ctx.close_path()
        else:
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
    else:
        rounded(ctx, x, y, w, h, 14)
    ctx.set_source_rgba(*hexrgb(el["cor"]))
    ctx.fill()
    sh = to_pil(surf)
    canvas.alpha_composite(shadow_of(sh, 0, h * 0.06, h * 0.1, (0, 0, 0, 0.5)))
    canvas.alpha_composite(sh)
    draw_simple(canvas, {**el, "b": [el["b"][0] + el["b"][2] * 0.15, el["b"][1] + el["b"][3] * 0.25, el["b"][2] * 0.7, el["b"][3] * 0.5]},
                W, H, el["cor2"], shadow=False)


def render(spec_path, out_path):
    t0 = time.time()
    spec = json.load(open(spec_path, encoding="utf-8"))
    W, H = spec["tamanho"]
    canvas = Image.open(spec["cenario"]).convert("RGBA").resize((W, H), Image.LANCZOS)  # base sem textos (ou cenário)
    want = []
    for el in spec["elementos"]:
        fam = _family(el.get("fonte"), el.get("familia") or "Sans")
        bold = (el.get("peso") or 400) >= 650
        texts = []
        if el["tipo"] in ("texto", "nome", "info", "selo") and el.get("texto"):
            texts += [ln for c in _candidates(el["texto"]) for ln in c.split("\n")]
        if el["tipo"] == "preco":
            texts += ["R$", el["inteiro"], (el["centavos"] or "") + (" " + el["sufixo"] if el["sufixo"] else ""), "de " + el["de"] if el.get("de") else ""]
        if el["tipo"] == "lista":
            fp = _family(el.get("fonte_preco"), el.get("familia_preco") or "Sans")
            want += [(a, fam, False) for a, _ in el["linhas"]] + [(b, fp, False) for _, b in el["linhas"]]
        want += [(t, fam, bold) for t in texts if t and t.strip()]
    prefetch(want)
    for el in spec["elementos"]:
        t = el["tipo"]
        if t == "texto":
            draw_text(canvas, el, W, H)
        elif t in ("nome", "info"):
            draw_simple(canvas, el, W, H, el.get("cor") or "#ffffff")
        elif t == "preco":
            draw_price(canvas, el, W, H)
        elif t == "produto":
            draw_product(canvas, el, W, H)
        elif t == "lista":
            draw_list(canvas, el, W, H)
        elif t == "selo":
            draw_seal(canvas, el, W, H)
    canvas.convert("RGB").save(out_path)
    return round(time.time() - t0, 2)


if __name__ == "__main__":
    print("@@", render(sys.argv[1], sys.argv[2]))
