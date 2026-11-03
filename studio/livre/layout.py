"""Resolvedor: transforma o plano semântico em geometria (em % da peça), preservando a hierarquia.

Entrada: plano normalizado (plano.normalize), briefing, fotos (proporção e qualidade) e tamanho da peça.
Saída: elementos posicionados + zonas reservadas aos objetos do cenário + linha da superfície de apoio.
Regras gerais (valem para qualquer arte): tamanhos pela prioridade; herói claramente maior; preço principal grande e
nunca reduzido para caber; elementos secundários cedem primeiro (movem, depois encolhem até um piso); nada fora da peça.
"""
M = 4.0  # margem da peça (%)
PROTECTED = ("preco_principal", "heroi", "titulo")


def _box(x, y, w, h):
    return [float(x), float(y), float(w), float(h)]


def overlap(a, b):
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, ox), max(0.0, oy)


def _el(tipo, box, papel, prio, **kw):
    return {"tipo": tipo, "b": box, "papel": papel, "prio": prio, "min": kw.pop("min", 0.7), "z": kw.pop("z", 10), **kw}


def solve(plan, briefing, photos, size):
    """photos: {indice: {"aspecto": w/h, "qualidade": "boa"|"fraca"}}. Devolve dict com elementos e zonas."""
    W, H = size
    k = W / H                      # 1% de largura vale k% de altura
    prods = briefing.get("produtos") or []
    lay = plan["layout"]
    good = {i for i, p in photos.items() if p.get("qualidade") in ("boa", "cartao")}  # cartão: foto sem recorte
    hero = plan["heroi"]
    others = [i for i in range(len(prods)) if i != hero]
    photo_others = [i for i in others if i in good]
    text_items = [i for i in others if i not in good]
    prio = plan["prioridades"]
    rank = {p: i for i, p in enumerate(prio)}
    els = []

    # 1) título (+ linha da empresa), tamanho pela prioridade e pela quantidade de produtos
    n_items = len(prods)
    th = 22.0 if n_items == 0 else 19.0 if n_items == 1 else 14.5
    th *= 1.12 if rank.get("titulo", 9) <= 1 else 0.9 if rank.get("titulo", 9) >= 3 else 1.0
    tz = lay["titulo"]
    if tz in ("esquerda", "direita") and n_items:
        tw, th = 44.0, th * 1.8
        tx = M if tz == "esquerda" else 100 - M - tw
        ty = M + 2
    else:
        tw, tx = 100 - 2 * M, M
        ty = {"topo": M, "base": 100 - M - th, "centro": 30.0}.get(tz, M)
    els.append(_el("texto", _box(tx, ty, tw, th), "titulo", 0, min=0.85, z=40, **plan["titulo"]))
    content = [M, M, 100 - 2 * M, 100 - 2 * M]  # x, y, w, h livres para o resto
    if tz == "topo":
        content = [M, ty + th + 1.5, 100 - 2 * M, 100 - M - (ty + th + 1.5)]
    elif tz == "base":
        content = [M, M, 100 - 2 * M, ty - 1.5 - M]
    elif tz in ("esquerda", "direita") and n_items:
        content = [M if tz == "direita" else tx + tw + 2, M, 100 - 2 * M - tw - 2, 100 - 2 * M]
    if plan["loja"]:
        lh = 4.6
        ly = ty + th + 0.6 if tz != "base" else ty - lh - 0.6
        els.append(_el("texto", _box(tx + tw * 0.15, ly, tw * 0.7, lh), "loja", 5, min=0.8, z=40, fonte="poppins",
                       ef="sombra", cor=plan["titulo"]["cor"], cor2="#000000"))
        if tz == "topo":
            content[1] += lh + 0.8; content[3] -= lh + 0.8
        elif tz == "base":
            content[3] -= lh + 0.8
    if plan["chamada"]:
        chh = 6.0
        if tz == "topo" or tz in ("esquerda", "direita"):
            cy = content[1]; content[1] += chh + 0.8; content[3] -= chh + 0.8
        elif tz == "base":
            cy = ty - chh - (6 if plan["loja"] else 1); content[3] -= chh + 0.8
        else:
            cy = ty + th + 1.5
        els.append(_el("texto", _box(content[0] + content[2] * 0.17, cy, content[2] * 0.66, chh), "chamada", 3, min=0.75, z=40,
                       **{k_: v for k_, v in plan["chamada"].items() if k_ != "txt"}, txt=plan["chamada"]["txt"]))

    cx0, cy0, cw, ch = content
    props_zones, surface = [], None

    if n_items == 0:
        # institucional: o cenário é o herói; selo e ícones perto do título; informações oficiais embaixo
        info = briefing.get("info") or {}
        lines = [f"{k_.capitalize()}: {v}" for k_, v in info.items() if v][:3]
        if lines:
            ih = 4.2 * len(lines) + 2
            els.append(_el("info", _box(cx0 + cw * 0.1, 100 - M - ih, cw * 0.8, ih), "info", 4, min=0.8, z=30, linhas=lines))
        if lay["objetos"] != "nenhum":
            props_zones.append(_box(cx0 + cw * 0.2, cy0 + ch * 0.25, cw * 0.6, ch * 0.45))
    else:
        # 2) divisão do espaço entre herói e demais, conforme a relação escolhida
        rel = lay["demais"] if (photo_others or text_items) else "nenhum"
        hero_area = [cx0, cy0, cw, ch]
        rest_area = None
        if rel == "coluna_lateral":
            side_w = cw * 0.34
            if lay["heroi"] == "direita":
                rest_area = [cx0, cy0, side_w, ch]; hero_area = [cx0 + side_w + 1.5, cy0, cw - side_w - 1.5, ch]
            else:
                rest_area = [cx0 + cw - side_w, cy0, side_w, ch]; hero_area = [cx0, cy0, cw - side_w - 1.5, ch]
        elif rel in ("fila_base", "grade", "arco"):
            frac = (0.29 if rel != "grade" else 0.40) if photo_others else 0.0
            if len(photo_others) == 1:
                frac = 0.24
            rest_area = [cx0, cy0 + ch * (1 - frac), cw, ch * frac] if photo_others else None
            hero_area = [cx0, cy0, cw, ch * (1 - frac) - (1.0 if photo_others else 0)]
        # orçamento vertical: se o herói (foto boa) não alcança o piso de área, os secundários cedem altura primeiro
        if hero in good and rest_area and rel != "coluna_lateral":
            ar_h = photos[hero]["aspecto"]
            floor_h = ((9.5 if n_items > 1 else 16.0) * 100 * k / ar_h) ** 0.5  # altura da foto que dá o piso de área
            need = floor_h + (5.2 if len(prods[hero]["nome"]) <= 22 else 8.0) + (6.0 * len(text_items) + 2.0 if text_items else 0) + 2
            if hero_area[3] < need:
                take = min(need - hero_area[3], rest_area[3] * 0.4)
                rest_area[1] += take; rest_area[3] -= take; hero_area[3] += take
        list_area = None
        if text_items:  # itens sem foto boa: faixa própria (nunca sobre os produtos)
            lh_ = 6.0 * len(text_items) + 2.0
            host = rest_area if rest_area and rel == "coluna_lateral" and rest_area[2] >= 50 else hero_area  # lista precisa de largura
            list_area = [host[0], host[1] + host[3] - lh_, host[2], lh_]
            host[3] -= lh_ + 1.2

        # 3) herói (foto boa) ou herói tipográfico
        hx0, hy0, hw, hh = hero_area
        p = prods[hero]
        price_rel = lay["preco"]
        name_h = 5.2 if len(p["nome"]) <= 22 else 8.0
        price_h_main = 15.0 if n_items == 1 else 12.0
        # piso de área do preço principal (% da peça), igual para qualquer relação escolhida pelo diretor
        min_area = 6.0 if n_items == 1 else 3.8

        def min_bw(ratio):
            return (min_area * 100 / (k * ratio)) ** 0.5
        if hero in good:
            ar = photos[hero]["aspecto"]
            side_room = hw * 0.5 - 2  # espaço ao lado do herói quando ele ocupa metade da área
            if price_rel in ("abaixo_heroi", "base") and hh - (name_h + price_h_main + 1) < hh * 0.62 and side_room >= min_bw(0.44):
                price_rel = "ao_lado_heroi"  # regra de espaço: a foto do herói não pode encolher por causa do preço
            elif price_rel == "ao_lado_heroi" and side_room < min_bw(0.44):
                price_rel = "abaixo_heroi"   # não cabe ao lado com o tamanho mínimo: vai para baixo
            # quanto sobra para a foto depois do nome e (se abaixo) do preço
            reserve = (name_h + (price_h_main + 1 if price_rel in ("abaixo_heroi", "base") else 0)) if price_rel != "ao_lado_heroi" else 0.0
            ph = max(18.0, hh - reserve - 1)
            pw = ph * ar / k
            maxw = hw * (0.5 if price_rel == "ao_lado_heroi" else 0.62 if lay["objetos"] in ("esquerda", "direita", "ambos") else 0.9)
            if pw > maxw:
                pw, ph = maxw, maxw * k / ar
            floor_area = 16.0 if n_items == 1 else 9.5  # piso do herói (% da peça): vence o espaço dos objetos decorativos
            if pw * ph / 100 < floor_area:
                f = min((floor_area * 100 / (pw * ph)) ** 0.5, (hw * 0.92) / pw, max(ph, hh - reserve) / ph)
                pw, ph = pw * f, ph * f
            hz = lay["heroi"]
            free_side = None
            if price_rel == "ao_lado_heroi" and hz == "centro":
                hz = "esquerda" if lay["objetos"] != "esquerda" else "direita"
            if hz == "centro":
                px = hx0 + (hw - pw) / 2
            elif hz == "esquerda":
                px = hx0 + 1
            else:
                px = hx0 + hw - pw - 1
            py = hy0 + max(0.0, (hh - reserve - ph) * 0.55)
            els.append(_el("produto", _box(px, py, pw, ph), "heroi", 1, min=0.85, z=20, produto=hero))
            surface = py + ph
            # nome + preço principal
            if price_rel == "ao_lado_heroi":
                left_space, right_space = px - hx0, hx0 + hw - (px + pw)
                side = "direita" if right_space >= left_space else "esquerda"
                bw = max(min_bw(0.44), 32.0, min(48.0, max(left_space, right_space) - 2))
                bx = px + pw + 1.0 if side == "direita" else px - bw - 1.0
                bx = max(M, min(bx, 100 - M - bw))
                bh = bw * k * 0.44
                by = py + ph * 0.42
                els.append(_el("preco", _box(bx, by, bw, bh), "preco_principal", 0, min=1.0, z=45, produto=hero))
                els.append(_el("nome", _box(bx, by - name_h - 0.6, bw, name_h), "nome", 4, min=0.75, z=44, produto=hero))
                free_side = "esquerda" if side == "direita" else "direita"
            elif price_rel == "sobre_heroi":
                bw = max(min_bw(0.42), 34.0, min(48.0, pw * 0.8))
                bh = bw * k * 0.42
                els.append(_el("preco", _box(px + pw - bw * 0.55, py + ph - bh * 0.9, bw, bh), "preco_principal", 0, min=1.0, z=45, produto=hero))
                els.append(_el("nome", _box(px, py + ph + 0.6, pw, name_h), "nome", 4, min=0.75, z=44, produto=hero))
            else:  # abaixo do herói ou faixa de base: preço grande, largura generosa
                bw = min(hw * 0.92, max(52.0, pw * 1.25, min_bw(0.36)))
                bh = max(min(price_h_main, bw * k * 0.36), min_area * 100 / bw)
                bx = hx0 + (hw - bw) / 2 if hz == "centro" else (px if hz == "esquerda" else px + pw - bw)
                bx = max(M, min(bx, 100 - M - bw))
                els.append(_el("nome", _box(max(M, min(px - 4, 100 - M - pw - 8)), py + ph + 0.6, pw + 8, name_h), "nome", 4, min=0.75, z=44, produto=hero))
                els.append(_el("preco", _box(bx, py + ph + name_h + 1.2, bw, bh), "preco_principal", 0, min=1.0, z=45, produto=hero))
            # zonas dos objetos do cenário ao lado do herói (a IA é instruída a pôr os objetos ali)
            sides = {"esquerda": ["esquerda"], "direita": ["direita"], "ambos": ["esquerda", "direita"]}.get(lay["objetos"], [])
            if free_side and free_side not in sides and sides:
                sides = [free_side]
            for s_ in sides:
                zx0 = hx0 if s_ == "esquerda" else px + pw + 1
                zx1 = px - 1 if s_ == "esquerda" else hx0 + hw
                if zx1 - zx0 > 10:
                    zh = ph * 0.55
                    props_zones.append(_box(zx0, py + ph - zh, zx1 - zx0, zh))
        else:
            # herói sem foto boa: nome em destaque + preço principal; o cenário é a imagem
            bw = min(hw * 0.9, 74.0)
            bh = max(min(price_h_main + 2, bw * k * 0.34), min_area * 100 / (bw * 0.8))
            ny = hy0 + hh * 0.38
            els.append(_el("nome", _box(hx0 + (hw - bw) / 2, ny, bw, name_h + 3), "nome_heroi", 2, min=0.8, z=44, produto=hero, destaque=True))
            els.append(_el("preco", _box(hx0 + (hw - bw * 0.8) / 2, ny + name_h + 4, bw * 0.8, bh), "preco_principal", 0, min=1.0, z=45, produto=hero))
            if lay["objetos"] != "nenhum":
                props_zones.append(_box(hx0 + hw * 0.1, hy0, hw * 0.8, hh * 0.36))

        # 4) demais produtos com foto: unidades (foto + preço ancorado + nome), distribuídas na área do resto
        if rest_area and photo_others:
            rx, ry, rw, rh = rest_area
            n = len(photo_others)
            if rel == "coluna_lateral":
                cell_h = rh / n
                for j, i in enumerate(photo_others):
                    _unit(els, i, prods[i], photos[i]["aspecto"], [rx, ry + j * cell_h, rw, cell_h - 1.0], k)
            elif rel == "grade" and n > 2:
                cols = 2 if n <= 4 else 3
                rows = (n + cols - 1) // cols
                for j, i in enumerate(photo_others):
                    r_, c_ = divmod(j, cols)
                    _unit(els, i, prods[i], photos[i]["aspecto"], [rx + c_ * rw / cols, ry + r_ * rh / rows, rw / cols - 1.0, rh / rows - 1.0], k)
            else:  # fila na base (ou arco: fila com deslocamento vertical em curva)
                cell_w = rw / n
                for j, i in enumerate(photo_others):
                    lift = 0.0
                    if rel == "arco" and n > 2:
                        t = (j / (n - 1)) * 2 - 1
                        lift = (1 - t * t) * rh * 0.18
                    _unit(els, i, prods[i], photos[i]["aspecto"], [rx + j * cell_w, ry - lift, cell_w - 1.2, rh], k)
        # 5) itens sem foto boa: um painel-lista elegante (nunca caixas vazias nem fotos ruins)
        if text_items and list_area:
            pw_ = min(list_area[2], 82.0)
            els.append(_el("lista", _box(list_area[0] + (list_area[2] - pw_) / 2, list_area[1], pw_, list_area[3]), "lista", 3,
                           min=0.8, z=35, itens=text_items))

    # 6) selo e ícones (secundários): perto do herói/título, do lado sem objetos
    if plan["selo"]:
        s = 17.0
        hero_el = next((e for e in els if e["papel"] == "heroi"), None)
        if hero_el:
            hb = hero_el["b"]
            # selo só encosta no canto superior do herói (cobre no máximo uma ponta da embalagem)
            sx = hb[0] - s * 0.7 if lay["objetos"] != "esquerda" else hb[0] + hb[2] - s * 0.3
            sy = hb[1] - s * k * 0.35
            if sx < M - 1 or sx + s > 100 - M + 1:  # sem espaço no canto escolhido: vai para o outro canto
                sx = hb[0] + hb[2] - s * 0.3 if sx < M - 1 else hb[0] - s * 0.7
        else:
            tb = els[0]["b"]
            sx, sy = tb[0] + tb[2] - s * 0.8, tb[1] + tb[3] - s * k * 0.2
        els.append(_el("selo", _box(sx, sy, s, s * k), "selo", 3, min=0.7, z=46, **plan["selo"]))
    if plan["icones"]:
        anchor = next((e for e in els if e["papel"] == "chamada"), None) or next((e for e in els if e["papel"] == "loja"), None) or els[0]
        ab = anchor["b"]
        isz = min(7.0, ab[3] / k * 1.1) if anchor["papel"] != "titulo" else 7.0
        slots = []
        for j in range(len(plan["icones"])):
            left = j % 2 == 0
            off = (j // 2) * (isz + 1.5)
            sx = ab[0] - isz - 1.5 - off if left else ab[0] + ab[2] + 1.5 + off
            slots.append(sx)
        for name, sx in zip(plan["icones"], slots):
            if M - 1 <= sx <= 100 - M - isz + 1:
                els.append(_el("icone", _box(sx, ab[1] + (ab[3] - isz * k) / 2, isz, isz * k), "icone", 6, min=0.6, z=30,
                               nome=name, cor=plan["titulo"]["cor"]))

    _enforce_dominance(els)
    relax(els, props_zones, k)
    return {"elementos": els, "zonas_objetos": props_zones, "superficie": surface}


def _enforce_dominance(els, ratio=1.9):  # margem sobre o mínimo de 1,8x
    """Regra geral: o herói tem pelo menos `ratio` vezes a área de qualquer outro produto. Os secundários encolhem
    em torno da base (com preço e nome acompanhando); o herói nunca é reduzido."""
    hero = next((e for e in els if e["papel"] == "heroi"), None)
    if not hero:
        return
    ha = hero["b"][2] * hero["b"][3]
    for e in [x for x in els if x["papel"] == "produto"]:
        a = e["b"][2] * e["b"][3]
        if a * ratio <= ha:
            continue
        f = (ha / (ratio * a)) ** 0.5
        b = e["b"]
        cx, by_ = b[0] + b[2] / 2, b[1] + b[3]
        nb = [cx - b[2] * f / 2, by_ - b[3] * f, b[2] * f, b[3] * f]
        dx, dy = nb[0] - b[0], nb[1] - b[1]
        e["b"] = nb
        for o in els:  # preço do mesmo produto acompanha a foto (continua no canto dela)
            if o is not e and o.get("produto") == e.get("produto") and o["tipo"] == "preco":
                o["b"][0] += dx * 0.5 + (b[2] - nb[2]) * 0.5; o["b"][1] += dy


def _unit(els, i, p, ar, cell, k):
    """Produto secundário: foto na proporção real + preço legível + nome. A orientação sai da forma da célula:
    célula larga -> foto à esquerda, preço e nome à direita; célula alta -> foto em cima, preço no canto, nome embaixo."""
    x, y, w, h = cell
    wide = (w * k) / max(h, 1e-6) > 1.25  # proporção real da célula em pixels
    if wide:
        pw = w * 0.46
        ph = pw * k / ar
        if ph > h:
            ph, pw = h, h * ar / k
        px, py = x, y + (h - ph)
        tx, tw = px + pw + 1.0, x + w - (px + pw + 1.0)
        bw = min(tw, 30.0)
        bh = min(bw * k * 0.40, h * 0.5)
        name_h = min(h - bh - 1.0, 6.6 if len(p["nome"]) > 18 else 4.4)
        by_ = y + h - bh - name_h - 1.0
        els.append(_el("produto", _box(px, py, pw, ph), "produto", 4, min=0.75, z=20, produto=i))
        els.append(_el("preco", _box(tx, by_, bw, bh), "preco", 2, min=0.95, z=42, produto=i))
        els.append(_el("nome", _box(tx, by_ + bh + 0.6, tw, name_h), "nome", 5, min=0.75, z=41, produto=i))
        return
    name_h = 4.2 if len(p["nome"]) <= 18 else 6.2
    ph = max(8.0, h - name_h - 0.6)
    pw = ph * ar / k
    if pw > w * 0.86:
        pw, ph = w * 0.86, w * 0.86 * k / ar
    px = x + (w - pw) / 2
    py = y + (h - name_h - 0.6 - ph)
    els.append(_el("produto", _box(px, py, pw, ph), "produto", 4, min=0.75, z=20, produto=i))
    bw = max(17.0, min(pw * 0.9, w * 0.7, 28.0))
    bh = min(bw * k * 0.40, ph * 0.42)  # o preço cobre no máximo ~40% da foto
    els.append(_el("preco", _box(min(px + pw - bw * 0.55, x + w - bw, 100 - M - bw), py + ph * 0.04, bw, bh),
                   "preco", 2, min=0.95, z=42, produto=i))
    els.append(_el("nome", _box(x, py + ph + 0.6, w, name_h), "nome", 5, min=0.75, z=41, produto=i))


def relax(els, fixed_zones, k, iters=300):
    """Separa colisões sem destruir a hierarquia: quem tem menor prioridade se move (e só ele encolhe, até o piso).
    Fotos podem se sobrepor um pouco (profundidade); o preço pode ficar sobre a PRÓPRIA foto."""
    obst = [{"b": list(z), "prio": -1, "tipo": "zona", "papel": "zona"} for z in fixed_zones]
    allu = els + obst
    for _ in range(iters):
        moved = False
        for a_i, a in enumerate(allu):
            for b in allu[a_i + 1:]:
                if a["tipo"] == "zona" and b["tipo"] == "zona":
                    continue
                if _allowed(a, b):
                    continue
                ox, oy = overlap(a["b"], b["b"])
                if ox <= 0.2 or oy <= 0.2:
                    continue
                if a["tipo"] == "zona" and b["tipo"] not in ("produto", "preco", "nome", "lista", "selo"):
                    continue  # textos podem passar por cima de objetos do cenário; produtos não
                if b["tipo"] == "zona" and a["tipo"] not in ("produto", "preco", "nome", "lista", "selo"):
                    continue
                mover, fixed = (a, b) if _rank(a) > _rank(b) else (b, a)
                if fixed["tipo"] == "zona" and mover["papel"] in PROTECTED:
                    continue  # herói/preço principal não fogem das zonas: as zonas é que foram planejadas ao lado
                _push(mover, fixed, ox, oy, k)
                moved = True
        if not moved:
            break
    for e in els:
        _clamp(e)


def _allowed(a, b):
    pair = {a["tipo"], b["tipo"]}
    same = a.get("produto") is not None and a.get("produto") == b.get("produto")
    if same and pair <= {"produto", "preco", "nome"}:
        return True  # preço/nome sobre ou junto da própria foto
    if pair == {"produto"}:
        ox, oy = overlap(a["b"], b["b"])
        return ox * oy <= 0.08 * min(a["b"][2] * a["b"][3], b["b"][2] * b["b"][3])  # leve sobreposição dá profundidade
    if pair == {"icone"} or "zona" in pair and pair & {"texto", "icone"}:
        return True
    if "selo" in pair and "produto" in pair:
        return True  # selo pode encostar na foto (é um carimbo sobre ela)
    return False


def _rank(e):
    return e["prio"] if e["tipo"] != "zona" else -1


def _push(m, f, ox, oy, k):
    """Move m para fora de f pelo eixo de menor penetração; se encostar na borda, encolhe (só se não protegido)."""
    mb, fb = m["b"], f["b"]
    before = list(mb)
    if ox < oy * k:
        sgn = 1 if mb[0] + mb[2] / 2 >= fb[0] + fb[2] / 2 else -1
        mb[0] += sgn * (ox + 0.4)
    else:
        sgn = 1 if mb[1] + mb[3] / 2 >= fb[1] + fb[3] / 2 else -1
        mb[1] += sgn * (oy + 0.4)
    _clamp(m)
    if mb[:2] == before[:2] and m["papel"] not in PROTECTED:
        scale = m.get("_s", 1.0)
        if scale * 0.94 >= m["min"]:
            m["_s"] = scale * 0.94
            cx, cy = mb[0] + mb[2] / 2, mb[1] + mb[3] / 2
            mb[2] *= 0.94; mb[3] *= 0.94
            mb[0], mb[1] = cx - mb[2] / 2, cy - mb[3] / 2


def _clamp(e):
    b = e["b"]
    b[2] = min(b[2], 100 - 2 * M + 4); b[3] = min(b[3], 100 - 2 * M + 4)
    b[0] = max(M - 2, min(b[0], 100 - M + 2 - b[2]))
    b[1] = max(M - 2, min(b[1], 100 - M + 2 - b[3]))
