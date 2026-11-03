"""Leitura do cenário e conferência estética objetiva (com reparo dirigido a cada problema).

Nada aqui depende de categoria ou de exemplo: tudo é medido no cenário gerado e no desenho final.
  - scene_map(): mapa de ocupação do cenário (bordas, textura e cor que se destacam do entorno = objetos);
  - place(): antes de desenhar, o bloco do título (título, empresa, chamada, ícones) e o selo procuram, perto da posição
    planejada, a região mais calma do cenário (a hierarquia e a ordem vertical continuam as do plano);
  - check()/repair(): texto sobre objeto do cenário, preço escondendo o produto ou o nome, excesso decorativo,
    desproporção entre textos, paleta de textos dispersa, herói pequeno com espaço sobrando.
"""
import numpy as np
from PIL import Image, ImageFilter

from studio import htmlflyer

from .layout import M, PROTECTED, overlap

GRID = (108, 135)  # mapa com 1/10 da peça (feed 4:5); vale para qualquer formato (coordenadas em %)
HEADER = ("titulo", "loja", "chamada")
TEXTS = ("titulo", "loja", "chamada", "info")
BUSY = 0.32          # fração ocupada acima da qual o texto disputa com o cenário
GAIN = 0.12          # só move se a nova posição for claramente mais calma


def scene_map(img):
    """0..1 por célula: quanto aquela região do cenário tem objeto/detalhe (bordas + cor que destoa do entorno)."""
    small = img.convert("RGB").resize(GRID, Image.BILINEAR)
    g = np.asarray(small.convert("L"), dtype=np.float32)
    gy, gx = np.gradient(g)
    edges = np.hypot(gx, gy)
    rgb = np.asarray(small, dtype=np.float32)
    blur = np.asarray(small.filter(ImageFilter.GaussianBlur(9)), dtype=np.float32)
    distinct = np.abs(rgb - blur).sum(axis=2)

    def norm(a):
        top = max(float(np.percentile(a, 97)), 1e-3)
        a8 = Image.fromarray(np.clip(a / top * 255, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(2))
        a = np.asarray(a8, dtype=np.float32) / 255
        return np.clip(a / max(float(np.percentile(a, 97)), 1e-3), 0, 1)
    return np.maximum(norm(edges), norm(distinct) * 0.9)


def busy(smap, box):
    """Fração ocupada embaixo de uma caixa [x, y, w, h] em %."""
    H, W = smap.shape
    x, y, w, h = box
    x0, x1 = int(max(0, x) * W / 100), int(min(100, x + w) * W / 100)
    y0, y1 = int(max(0, y) * H / 100), int(min(100, y + h) * H / 100)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float((smap[y0:y1, x0:x1] > 0.45).mean())


def _key(e):
    return f'{e["papel"]}:{e.get("produto", "")}'


def _group_box(els):
    x0 = min(e["b"][0] for e in els); y0 = min(e["b"][1] for e in els)
    x1 = max(e["b"][0] + e["b"][2] for e in els); y1 = max(e["b"][1] + e["b"][3] for e in els)
    return [x0, y0, x1 - x0, y1 - y0]


def _collides(box, others, tol=0.6):
    return any(min(box[0] + box[2], o[0] + o[2]) - max(box[0], o[0]) > tol and
               min(box[1] + box[3], o[1] + o[3]) - max(box[1], o[1]) > tol for o in others)


def _best_shift(smap, group, obstacles, max_dy=16.0, max_dx=0.0, step=1.0):
    """Deslocamento (dx, dy) do grupo para a região mais calma por perto, sem colidir com o resto."""
    gb = _group_box(group)
    base = sum(busy(smap, e["b"]) * e["b"][2] * e["b"][3] for e in group) / max(1e-6, sum(e["b"][2] * e["b"][3] for e in group))
    best, best_cost = (0.0, 0.0), base
    dxs = np.arange(-max_dx, max_dx + 0.01, max(step, 2.0)) if max_dx else [0.0]
    for dy in np.arange(-max_dy, max_dy + 0.01, step):
        for dx in dxs:
            nb = [gb[0] + dx, gb[1] + dy, gb[2], gb[3]]
            if nb[0] < M - 1 or nb[1] < M - 1 or nb[0] + nb[2] > 101 - M or nb[1] + nb[3] > 101 - M or                     _collides(nb, [[o[0] - 1.5, o[1] - 2.0, o[2] + 3.0, o[3] + 4.0] for o in obstacles]):  # respiro entre blocos
                continue
            moved = [[e["b"][0] + dx, e["b"][1] + dy, e["b"][2], e["b"][3]] for e in group]
            cost = sum(busy(smap, b) * b[2] * b[3] for b in moved) / max(1e-6, sum(b[2] * b[3] for b in moved))
            cost += 0.003 * (abs(dy) + abs(dx))  # prefere ficar perto do que o diretor planejou
            if cost < best_cost - 1e-6:
                best, best_cost = (float(dx), float(dy)), cost
    return best, base, best_cost


def place(solved, smap):
    """Antes de desenhar: cabeçalho e selo vão para a região mais calma perto do planejado (nunca sobre produtos)."""
    els = solved["elementos"]
    moves = []
    header = [e for e in els if e["papel"] in HEADER]
    anchored = [e for e in els if e["tipo"] == "icone"]
    if header:
        others = [e["b"] for e in els if e not in header and e not in anchored]
        (dx, dy), before, after = _best_shift(smap, header, others, max_dy=55.0)
        if before - after >= GAIN:
            for e in header + anchored:
                e["b"][0] += dx; e["b"][1] += dy
            moves.append(("cabecalho", round(before, 2), round(after, 2), round(dy, 1)))
    info = [e for e in els if e["tipo"] == "info"]
    if info:
        others = [e["b"] for e in els if e not in info]
        (dx, dy), before, after = _best_shift(smap, info, others, max_dy=10.0)
        if before - after >= GAIN:
            for e in info:
                e["b"][1] += dy
            moves.append(("info", round(before, 2), round(after, 2), round(dy, 1)))
    return moves


# ------------------------------------------------------------------------------------------ conferência
def check(items, solved, size, smap=None, plan=None):
    """Problemas estéticos objetivos: (código, chave, detalhe)."""
    W, H = size
    rect = {i["k"]: [i["x"] * 100 / W, i["y"] * 100 / H, i["w"] * 100 / W, i["h"] * 100 / H] for i in items}
    font = {i["k"]: i.get("fonte") or 0 for i in items}
    by = {_key(e): e for e in solved["elementos"]}
    probs = []
    # 1) texto encostado/sobre objeto do cenário (medido pelas letras reais)
    if smap is not None:
        for k, r in rect.items():
            papel = k.split(":")[0]
            el = by.get(k) or {}
            if el.get("_fugiu") and el.get("reforco"):
                continue  # já tratado: não havia lugar calmo, ficou com contorno (legibilidade conferida à parte)
            if papel in TEXTS and busy(smap, r) > BUSY + 0.08:
                probs.append(("texto_sobre_objeto", k, round(busy(smap, r), 2)))
    # 2) preço escondendo o produto (ou o nome dele)
    for k, r in rect.items():
        papel, prod = k.split(":")
        if papel not in ("preco", "preco_principal") or prod == "":
            continue
        for pk in (f"heroi:{prod}", f"produto:{prod}"):
            if pk in rect:
                pr = rect[pk]
                ox, oy = overlap(r, pr)
                frac = ox * oy / max(1e-6, pr[2] * pr[3])
                if frac > (0.22 if pk.startswith("heroi") else 0.32):
                    probs.append(("preco_sobre_produto", k, round(frac, 2)))
        for nk in (f"nome:{prod}", f"nome_heroi:{prod}"):
            if nk in rect:  # encostar também conta: o nome precisa de respiro do preço (desenho medido)
                ox, oy = overlap([r[0] - 0.4, r[1] - 0.7, r[2] + 0.8, r[3] + 1.4], rect[nk])
                if ox * oy > 0.08 * rect[nk][2] * rect[nk][3]:
                    probs.append(("preco_sobre_nome", k, nk))
    # 3) excesso decorativo: ícones demais, ou ícone colado em texto
    icons = [e for e in solved["elementos"] if e["tipo"] == "icone"]
    n_dec = len(icons) + sum(1 for e in solved["elementos"] if e["tipo"] == "selo")
    n_prod = sum(1 for e in solved["elementos"] if e["tipo"] == "produto")
    if icons and (n_dec > 3 or (n_prod >= 3 and len(icons) > 1)):
        probs.append(("excesso_decorativo", _key(icons[-1]), n_dec))
    # 4) desproporção: a ordem de tamanho acompanha a hierarquia
    t = font.get("titulo:", 0)
    for k in ("chamada:", "loja:"):
        if t and font.get(k, 0) > t * 0.8:
            probs.append(("desproporcao", k, round(font[k] / t, 2)))
    if "loja:" in font and "chamada:" in font and font["loja:"] > font["chamada:"] * 1.15:
        probs.append(("desproporcao", "loja:", "maior que a chamada"))
    sel = next((k for k in rect if k.startswith("selo:")), None)
    pm = next((k for k in rect if k.startswith("preco_principal:")), None)
    if sel and pm and rect[sel][2] * rect[sel][3] > 0.75 * rect[pm][2] * rect[pm][3]:
        probs.append(("desproporcao", sel, "selo disputa com o preço"))
    # 5) paleta de textos dispersa (mais de 3 famílias de cor nos textos)
    if plan is not None:
        hues = _hue_families([e.get("cor") or (plan["titulo"]["cor"] if e["papel"] == "titulo" else None)
                              for e in solved["elementos"] if e["tipo"] in ("texto",)])
        if hues > 3:
            probs.append(("cores_dispersas", "chamada:", hues))
    # 6) herói pequeno com espaço livre ao redor (espaço mal aproveitado)
    hk = next((k for k in rect if k.startswith("heroi:")), None)
    if hk and smap is not None:
        hb = by.get(hk, {}).get("b")
        if hb and _room_to_grow(hb, [e["b"] for e in solved["elementos"] if _key(e) != hk and e.get("produto") != by[hk].get("produto")
                                     and e["tipo"] != "icone"], 1.12):
            area = rect[hk][2] * rect[hk][3] / 100
            if area < (22.0 if n_prod == 1 else 12.0):
                probs.append(("espaco_mal_aproveitado", hk, round(area, 1)))
    return probs


def _hue_families(colors):
    fams = set()
    for c in colors:
        if not c:
            continue
        h = htmlflyer._hex(c, "#ffffff")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        mx, mn = max(r, g, b), min(r, g, b)
        if mx - mn < 0.18:
            fams.add("neutro")
            continue
        if mx == r:
            hue = ((g - b) / (mx - mn)) % 6
        elif mx == g:
            hue = (b - r) / (mx - mn) + 2
        else:
            hue = (r - g) / (mx - mn) + 4
        fams.add(int(hue * 60 // 45))  # 8 famílias de matiz
    return len(fams - {"neutro"})


def _room_to_grow(b, others, f):
    cx, by_ = b[0] + b[2] / 2, b[1] + b[3]
    nb = [cx - b[2] * f / 2, by_ - b[3] * f, b[2] * f, b[3] * f]
    if nb[0] < M or nb[1] < M or nb[0] + nb[2] > 100 - M:
        return None
    return None if _collides(nb, others) else nb


def repair(solved, probs, items, size, smap=None, plan=None):
    """Reparo dirigido: cada código mexe só no elemento envolvido. Devolve quantos reparos foram feitos."""
    W, H = size
    by = {_key(e): e for e in solved["elementos"]}
    rect = {i["k"]: [i["x"] * 100 / W, i["y"] * 100 / H, i["w"] * 100 / W, i["h"] * 100 / H] for i in items}
    done = 0
    for code, k, det in probs:
        e = by.get(k)
        if code == "texto_sobre_objeto" and e and e.get("_fugiu"):
            e["reforco"] = e.get("reforco") or e.get("_reforco_cor") or "#111114"  # já moveu e ainda disputa: contorno
            done += 1
        elif code == "texto_sobre_objeto" and e and smap is not None:
            group = [x for x in solved["elementos"] if x["papel"] in HEADER] if e["papel"] in HEADER else [e]
            anchored = [x for x in solved["elementos"] if x["tipo"] == "icone"] if e["papel"] in HEADER else []
            others = [x["b"] for x in solved["elementos"] if x not in group and x not in anchored]
            # informações oficiais ficam no rodapé (só ajustam um pouco); o cabeçalho pode descer/subir mais
            (dx, dy), before, after = _best_shift(smap, group, others, max_dy=55.0 if e["papel"] in HEADER else 10.0)
            for x in group:
                x["_fugiu"] = True
            if before - after >= GAIN * 0.6:
                for x in group + anchored:
                    x["b"][0] += dx; x["b"][1] += dy
                done += 1
            else:
                e["reforco"] = e.get("reforco") or e.get("_reforco_cor") or "#111114"  # sem lugar calmo: contorno
                done += 1
        elif code == "preco_sobre_produto" and e:
            prod = k.split(":")[1]
            pk = next((x for x in (f"heroi:{prod}", f"produto:{prod}") if x in by), None)
            if pk and _price_aside(e, by[pk], solved):
                done += 1
        elif code == "preco_sobre_nome" and e:
            n = by.get(det)
            if n and n.get("_desceu", 0) < 2:
                # o nome sai de baixo do preço (pelo desenho MEDIDO do preço, com explosão/etiqueta); o preço fica
                pr, nr = rect.get(k) or e["b"], rect.get(det) or n["b"]
                if nr[1] + nr[3] / 2 >= pr[1] + pr[3] / 2:
                    dy = pr[1] + pr[3] + 0.6 - nr[1]
                else:
                    dy = pr[1] - 0.6 - (nr[1] + nr[3])
                photo = next((rect[x] for x in (f"heroi:{k.split(':')[1]}", f"produto:{k.split(':')[1]}") if x in rect), None)

                busy_rects = [rect.get(_key(x), x["b"]) for x in solved["elementos"]
                              if x is not n and x is not e and x["tipo"] not in ("icone", "produto")]

                def bad(d):  # nunca fora da peça, sobre a foto do produto ou em cima de outro bloco (sem vaivém)
                    nb = [nr[0], nr[1] + d, nr[2], nr[3]]
                    if nb[1] + nb[3] > 100 - M + 1 or nb[1] < M - 1:
                        return True
                    if photo:
                        ox, oy = overlap(nb, photo)
                        if ox * oy > 0.2 * nb[2] * nb[3]:
                            return True
                    return _collides(nb, busy_rects)
                if bad(dy):
                    alt = (pr[1] - 0.6 - (nr[1] + nr[3])) if dy > 0 else (pr[1] + pr[3] + 0.6 - nr[1])  # outro lado
                    if bad(alt):
                        # sem lugar para o nome: o preço (secundário ou principal) sobe um pouco, sem encolher
                        e["b"][1] -= (pr[1] + pr[3]) - nr[1] + 0.6 if nr[1] > pr[1] else 0
                        n["_desceu"] = 2
                        done += 1
                        continue
                    dy = alt
                n["b"][1] += dy
                n["_desceu"] = n.get("_desceu", 0) + 1
                done += 1
        elif code == "excesso_decorativo" and e:
            solved["elementos"].remove(e)
            done += 1
        elif code == "desproporcao" and e and e["papel"] not in PROTECTED and e.get("_s", 1.0) * 0.85 >= e.get("min", 0.6):
            b = e["b"]
            cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
            b[2] *= 0.85; b[3] *= 0.85; b[0], b[1] = cx - b[2] / 2, cy - b[3] / 2
            e["_s"] = e.get("_s", 1.0) * 0.85
            done += 1
        elif code == "cores_dispersas" and plan is not None:
            # chamada e empresa adotam a cor do título ou o neutro que contrastar (paleta coesa, sem cor nova)
            for x in solved["elementos"]:
                if x["papel"] in ("chamada", "loja") and not x.get("_paleta"):
                    light = htmlflyer._luma(x.get("cor") or "#ffffff") > 128  # mantém a luminosidade (o contraste continua)
                    x["cor"] = plan["titulo"]["cor"] if x["papel"] == "loja" and \
                        (htmlflyer._luma(plan["titulo"]["cor"]) > 128) == light else ("#ffffff" if light else "#1e1e22")
                    x["_paleta"] = True
                    done += 1
        elif code == "espaco_mal_aproveitado" and e and not e.get("_cresceu"):
            others = [x["b"] for x in solved["elementos"] if x is not e and x.get("produto") != e.get("produto") and x["tipo"] != "icone"]
            for f in (1.2, 1.12, 1.06):
                nb = _room_to_grow(e["b"], others, f)
                if nb:  # cresce a partir da base (apoio no mesmo lugar); preço e nome são conferidos na rodada seguinte
                    e["b"] = nb
                    done += 1
                    break
            e["_cresceu"] = True
    return done


def _price_aside(price, prod, solved):
    """Leva o preço para fora da foto (lado com espaço, depois abaixo), mantendo o tamanho."""
    pb, fb = price["b"], prod["b"]
    others = [x["b"] for x in solved["elementos"] if x is not price and x is not prod and x["tipo"] != "icone"]
    cands = [
        [fb[0] + fb[2] - pb[2] * 0.25, fb[1] + fb[3] - pb[3] * 0.6],   # canto inferior, encostando
        [fb[0] + fb[2] + 0.8, fb[1] + fb[3] * 0.45],                    # ao lado direito
        [fb[0] - pb[2] - 0.8, fb[1] + fb[3] * 0.45],                    # ao lado esquerdo
        [fb[0] + (fb[2] - pb[2]) / 2, fb[1] + fb[3] + 0.8],             # abaixo
    ]
    for x, y in cands:
        nb = [x, y, pb[2], pb[3]]
        if nb[0] < M - 1 or nb[1] < M - 1 or nb[0] + nb[2] > 101 - M or nb[1] + nb[3] > 101 - M:
            continue
        ox, oy = overlap(nb, fb)
        if ox * oy <= 0.18 * fb[2] * fb[3] and not _collides(nb, others):
            price["b"][0], price["b"][1] = nb[0], nb[1]
            return True
    return False
