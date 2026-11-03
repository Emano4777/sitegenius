"""Composições livres do encarte (posições absolutas): o diretor de arte escolhe e ajusta, o navegador desenha.

composicao (escolhida pelo modelo; a avaliação estética pode trocar):
  heroi_lateral  produto destaque grande à esquerda, demais empilhados à direita
  heroi_topo     destaque em faixa larga no topo, demais abaixo
  diagonal       produtos ao longo de uma faixa inclinada, alternando lados, com leve rotação e sobreposição
  mosaico        tamanhos variados (destaque ocupa 2x2)
  vitrine        produtos soltos (recortados) lado a lado sobre uma base, selo de preço sobreposto à foto
  grade          grade uniforme (para muitos produtos)
Parâmetros: destaque (produto), proporcao_destaque (0.45-0.7), angulo (-14..14), sobreposicao (0-0.12), estilo_unidade
(cartao|solto). Produtos SEM foto viram unidades tipográficas compactas (nome + preço), agrupadas numa faixa de
"mais ofertas": o espaço vai para quem tem foto e nada de caixa vazia ou embalagem inventada.
Saída: lista de unidades {indice, x, y, w, h, rot, z, tipo: foto_cartao|foto_solta|foto_horizontal|tipografica, heroi}.
"""
import math

COMPOSITIONS = ("heroi_lateral", "heroi_topo", "diagonal", "mosaico", "vitrine", "grade")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def plan_units(products, has_photo, area, comp, params):
    """Posições (px) das unidades dentro da área (x0, y0, w, h)."""
    x0, y0, W, H = area
    gap = params.get("gap", 22)
    photo_idx = [i for i, p in enumerate(products) if has_photo[i]]
    text_idx = [i for i, p in enumerate(products) if not has_photo[i]]
    hero_name = params.get("destaque")
    hero = next((i for i in photo_idx if hero_name and products[i]["nome"].lower() == str(hero_name).lower()), None)
    if hero is None and comp in ("heroi_lateral", "heroi_topo", "mosaico") and photo_idx:
        hero = photo_idx[0]
    units = []
    # Faixa tipográfica para produtos sem foto (compacta; altura proporcional à quantidade).
    strip_h = 0
    if text_idx:
        if not photo_idx:
            strip_h = H
        else:
            per_row = 2 if len(text_idx) > 2 and W > 700 else 1
            rows = math.ceil(len(text_idx) / per_row)
            strip_h = _clamp(rows * H * 0.105 + (rows - 1) * gap * 0.6, H * 0.12, H * 0.42)
        units += _text_strip(text_idx, (x0, y0 + H - strip_h, W, strip_h), gap, alone=not photo_idx)
    if not photo_idx:
        return units
    RH = H - strip_h - (gap if strip_h else 0)
    rect = (x0, y0, W, RH)
    ordered = ([hero] if hero is not None else []) + [i for i in photo_idx if i != hero]
    style = "foto_solta" if params.get("estilo_unidade") == "solto" else "foto_cartao"
    builder = {"heroi_lateral": _hero_side, "heroi_topo": _hero_top, "diagonal": _diagonal, "mosaico": _mosaic,
               "vitrine": _showcase, "grade": _grid}.get(comp, _grid)
    units += builder(ordered, rect, gap, params, style, hero)
    return units


def _unit(i, x, y, w, h, kind, hero=False, rot=0.0, z=2):
    return {"indice": i, "x": round(x), "y": round(y), "w": round(w), "h": round(h), "tipo": kind, "heroi": hero,
            "rot": round(rot, 1), "z": z}


def _shape_for(w, h, style):
    """Retângulo largo: foto ao lado do preço; alto: foto em cima."""
    return "foto_horizontal" if w > h * 1.35 else style


def _text_strip(idx, rect, gap, alone=False):
    x0, y0, W, H = rect
    n = len(idx)
    cols = 1 if alone and n <= 4 else (2 if n > 2 and W > 700 else 1)
    rows = math.ceil(n / cols)
    cw = (W - gap * (cols - 1)) / cols
    # Sem nenhuma foto: o cartaz tipográfico ocupa a área toda (linhas altas, sem faixas vazias).
    ch = (H - gap * 0.6 * (rows - 1)) / rows if alone else min((H - gap * 0.6 * (rows - 1)) / rows, 220)
    top = y0 + (H - (rows * ch + (rows - 1) * gap * 0.6)) / 2
    return [_unit(i, x0 + (k % cols) * (cw + gap), top + (k // cols) * (ch + gap * 0.6), cw, ch, "tipografica")
            for k, i in enumerate(idx)]


def _grid(ordered, rect, gap, params, style, hero):
    x0, y0, W, H = rect
    n = len(ordered)
    best = None
    for cols in range(1, 5):
        rows = math.ceil(n / cols)
        cw, ch = (W - gap * (cols - 1)) / cols, (H - gap * (rows - 1)) / rows
        side = min(cw * 0.5, ch * 0.88) if cw > ch * 1.35 else min(cw * 0.9, ch * 0.6)
        empty = (cols * rows - n) / (cols * rows)
        score = side * side * (1 - 0.8 * empty)
        if best is None or score > best[0]:
            best = (score, cols, rows, cw, ch)
    _, cols, rows, cw, ch = best
    out = []
    for k, i in enumerate(ordered):
        r, c = divmod(k, cols)
        in_row = min(cols, n - r * cols)
        ox = x0 + (W - (in_row * cw + gap * (in_row - 1))) / 2
        out.append(_unit(i, ox + c * (cw + gap), y0 + r * (ch + gap), cw, ch, _shape_for(cw, ch, style)))
    return out


def _hero_side(ordered, rect, gap, params, style, hero):
    x0, y0, W, H = rect
    if len(ordered) == 1:
        return [_unit(ordered[0], x0, y0, W, H, _shape_for(W, H, style), True)]
    ratio = _clamp(float(params.get("proporcao_destaque") or 0.56), 0.45, 0.7)
    hw = W * ratio
    rest = ordered[1:]
    ch = (H - gap * (len(rest) - 1)) / len(rest)
    ov = _clamp(float(params.get("sobreposicao") or 0.04), 0, 0.12) * W
    out = [_unit(ordered[0], x0, y0, hw + ov, H, style, True, z=3)]
    for k, i in enumerate(rest):
        out.append(_unit(i, x0 + hw + gap, y0 + k * (ch + gap), W - hw - gap, ch, _shape_for(W - hw - gap, ch, style)))
    return out


def _hero_top(ordered, rect, gap, params, style, hero):
    x0, y0, W, H = rect
    if len(ordered) == 1:
        return [_unit(ordered[0], x0, y0, W, H, _shape_for(W, H, style), True)]
    ratio = _clamp(float(params.get("proporcao_destaque") or 0.52), 0.4, 0.68)
    hh = H * ratio
    out = [_unit(ordered[0], x0, y0, W, hh, "foto_horizontal" if W > hh * 1.2 else style, True, z=3)]
    out += _grid(ordered[1:], (x0, y0 + hh + gap, W, H - hh - gap), gap, params, style, None)
    return out


def _mosaic(ordered, rect, gap, params, style, hero):
    """Destaque grande à esquerda, até 2 produtos na coluna da direita e o restante numa faixa inferior que ocupa a
    largura toda (sem buracos)."""
    x0, y0, W, H = rect
    n = len(ordered)
    if n <= 2:
        return _hero_side(ordered, rect, gap, params, style, hero)
    rest = ordered[1:]
    right = rest[:2]
    below = rest[2:]
    top_h = H if not below else H * (0.62 if len(below) <= 3 else 0.55)
    hw = (W - gap) * 0.62
    out = [_unit(ordered[0], x0, y0, hw, top_h, style, True, z=3)]
    rh = (top_h - gap * (len(right) - 1)) / len(right)
    for k, i in enumerate(right):
        out.append(_unit(i, x0 + hw + gap, y0 + k * (rh + gap), W - hw - gap, rh, _shape_for(W - hw - gap, rh, style)))
    if below:
        out += _grid(below, (x0, y0 + top_h + gap, W, H - top_h - gap), gap, params, style, None)
    return out


def _diagonal(ordered, rect, gap, params, style, hero):
    x0, y0, W, H = rect
    n = len(ordered)
    ang = _clamp(float(params.get("angulo") or -8), -14, 14)
    ov = _clamp(float(params.get("sobreposicao") or 0.06), 0, 0.12)
    uh = H / n * (1 + ov * 2)
    uw = W * (0.72 if n > 1 else 1.0)
    out = []
    for k, i in enumerate(ordered):
        t = k / max(1, n - 1)
        x = x0 + (W - uw) * (t if ang < 0 else 1 - t)
        y = y0 + k * (H - uh) / max(1, n - 1) if n > 1 else y0
        out.append(_unit(i, x, y, uw, uh, "foto_horizontal", k == 0, rot=ang * 0.45 * (1 if k % 2 == 0 else -0.6), z=2 + k))
    return out


def _showcase(ordered, rect, gap, params, style, hero):
    x0, y0, W, H = rect
    n = len(ordered)

    def photo_side(pr):
        rr = math.ceil(n / pr)
        w = (W - gap * (pr - 1)) / pr
        h = min((H - gap * (rr - 1)) / rr, w * 1.75)
        return min(w * 0.92, h * 0.68)
    per_row = max({n, math.ceil(n / 2), math.ceil(n / 3)} - {0}, key=photo_side)  # fileiras que deixam a foto maior
    rows = math.ceil(n / per_row)
    out = []
    avg_w = (W - gap * (per_row - 1)) / per_row
    rh = min((H - gap * (rows - 1)) / rows, avg_w * 1.75)  # unidade não fica mais alta que o produto pede
    top = y0 + (H - (rows * rh + gap * (rows - 1))) / 2
    k = 0
    for r in range(rows):
        items = ordered[k:k + per_row]
        weights = [1.35 if (i == hero) else 1.0 for i in items]
        total = sum(weights)
        x = x0
        for i, wgt in zip(items, weights):
            w = (W - gap * (len(items) - 1)) * wgt / total
            out.append(_unit(i, x, top + r * (rh + gap), w, rh, "foto_solta", i == hero, z=3 if i == hero else 2))
            x += w + gap
        k += per_row
    return out
