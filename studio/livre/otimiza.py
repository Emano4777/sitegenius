"""Layout dinâmico: o diretor decide a direção criativa (hierarquia, relações, ideia); aqui se procura a solução
espacial que melhor a realiza NO CENÁRIO REAL, sem posições fixas.

Os elementos ficam agrupados como o diretor os relacionou (cabeçalho; produto + preço + nome; cada secundário; lista;
informações) e cada grupo pode se mover e, quando faz sentido, crescer. Uma busca (recozimento simulado) minimiza:
  - sobreposição entre grupos e falta de respiro (restrição forte); nada fora da peça;
  - texto sobre objetos/detalhes do cenário (mapa de ocupação);
  - produto principal abaixo da área-alvo (o produto precisa aparecer);
  - desequilíbrio visual (centro de massa longe do eixo);
  - troca da ordem de leitura planejada (cabeçalho -> produto -> demais);
  - distância da composição planejada (a intenção do diretor vale).
Determinístico pela semente do briefing.
"""
import math
import random

from . import estetica
from .layout import M

GAP = 1.4  # respiro mínimo entre grupos (% da peça)


def _key(e):
    return f'{e["papel"]}:{e.get("produto", "")}'


def groups(solved):
    els = solved["elementos"]
    out, used = [], set()

    def add(name, members, kind, scale_range=(1.0, 1.0)):
        members = [m for m in members if id(m) not in used]
        if members:
            used.update(id(m) for m in members)
            out.append({"nome": name, "els": members, "tipo": kind, "escala": scale_range})
    add("cabecalho", [e for e in els if e["papel"] in ("titulo", "loja", "chamada")] + [e for e in els if e["tipo"] == "icone"], "texto")
    hero = next((e for e in els if e["papel"] in ("heroi",)), None)
    hero_prod = next((e.get("produto") for e in els if e["papel"] in ("heroi", "preco_principal")), None)
    if hero_prod is not None:
        add("principal", [e for e in els if e.get("produto") == hero_prod and e["tipo"] in ("produto", "preco", "nome")],
            "unidade", (1.0, 1.25) if hero else (1.0, 1.0))
    for e in els:
        if e["tipo"] == "produto" and e.get("produto") != hero_prod:
            add(f"item{e['produto']}", [x for x in els if x.get("produto") == e["produto"]], "unidade", (0.92, 1.08))
    for kind in ("lista", "info"):
        add(kind, [e for e in els if e["tipo"] == kind], "texto")
    add("selo", [e for e in els if e["tipo"] == "selo"], "selo")
    return out


def _bbox(boxes):
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    return [x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0]


def _apply(g, st):
    """Caixas do grupo no estado (dx, dy, s): escala em torno da base-centro do grupo (apoio no mesmo lugar)."""
    dx, dy, s = st
    bb = g["bb"]
    cx, by = bb[0] + bb[2] / 2, bb[1] + bb[3]
    return [[cx + (b[0] - cx) * s + dx, by + (b[1] - by) * s + dy, b[2] * s, b[3] * s] for b in g["orig"]]


def _ov(a, b):
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, ox) * max(0.0, oy)


def refine(solved, smap, size, seed=0, iters=2600):
    """Reposiciona os grupos no cenário real. Devolve um relatório (custo antes/depois e o que mudou)."""
    gs = groups(solved)
    if not gs:
        return {"grupos": 0}
    for g in gs:
        g["orig"] = [list(e["b"]) for e in g["els"]]
        g["bb"] = _bbox(g["orig"])
    order = sorted(range(len(gs)), key=lambda i: gs[i]["bb"][1] + gs[i]["bb"][3] / 2)  # ordem de leitura planejada
    rank = {i: r for r, i in enumerate(order)}
    hero_g = next((g for g in gs if g["nome"] == "principal"), None)
    hero_el = next((e for e in solved["elementos"] if e["papel"] == "heroi"), None)
    n_prod = sum(1 for e in solved["elementos"] if e["tipo"] == "produto")
    hero_target = (24.0 if n_prod == 1 else 13.0) if hero_el else 0

    def cost(states, detail=False):
        boxes = [_apply(g, s) for g, s in zip(gs, states)]
        bbs = [_bbox(b) for b in boxes]
        c = {"sobreposicao": 0.0, "fora": 0.0, "cenario": 0.0, "heroi": 0.0, "equilibrio": 0.0, "ordem": 0.0, "intencao": 0.0}
        for i in range(len(gs)):
            bi = bbs[i]
            c["fora"] += max(0, M - 1 - bi[0]) + max(0, M - 1 - bi[1]) + max(0, bi[0] + bi[2] - 101 + M) + max(0, bi[1] + bi[3] - 101 + M)
            for j in range(i + 1, len(gs)):
                if {gs[i]["tipo"], gs[j]["tipo"]} == {"selo", "unidade"}:
                    continue  # selo pode carimbar o canto do produto
                pad = [bbs[j][0] - GAP, bbs[j][1] - GAP, bbs[j][2] + 2 * GAP, bbs[j][3] + 2 * GAP]
                c["sobreposicao"] += _ov(bi, pad)
        for g, bx in zip(gs, boxes):  # texto sobre o cenário (fotos de produto podem ficar sobre o cenário)
            for e, b in zip(g["els"], bx):
                if e["tipo"] in ("texto", "preco", "nome", "lista", "info") and smap is not None:
                    c["cenario"] += estetica.busy(smap, b) * min(b[2] * b[3], 900) / 100
        if hero_g is not None and hero_el is not None:
            hb = _apply(hero_g, states[gs.index(hero_g)])[hero_g["els"].index(hero_el)]
            c["heroi"] = max(0.0, hero_target - hb[2] * hb[3] / 100)
        tot = sum(b[2] * b[3] for b in bbs) or 1
        cx = sum((b[0] + b[2] / 2) * b[2] * b[3] for b in bbs) / tot
        c["equilibrio"] = max(0.0, abs(cx - 50) - 6) / 10
        ys = [b[1] + b[3] / 2 for b in bbs]
        for i in range(len(gs)):
            for j in range(len(gs)):
                if rank[i] < rank[j] and ys[i] > ys[j] + 2 and gs[i]["tipo"] != "selo" and gs[j]["tipo"] != "selo":
                    c["ordem"] += 1
        c["intencao"] = sum(s[0] ** 2 + s[1] ** 2 for s in states) / 400 + sum(abs(s[2] - 1) for s in states) * 0.5
        w = {"sobreposicao": 3.0, "fora": 8.0, "cenario": 1.2, "heroi": 0.35, "equilibrio": 1.0, "ordem": 4.0, "intencao": 1.0}
        total = sum(w[k] * v for k, v in c.items())
        return (total, c) if detail else total

    rng = random.Random(seed)
    cur = [(0.0, 0.0, 1.0) for _ in gs]
    cur_c = cost(cur)
    start_c, start_detail = cost(cur, True)
    best, best_c = list(cur), cur_c
    for it in range(iters):
        T = 2.0 * (1 - it / iters) + 0.02
        i = rng.randrange(len(gs))
        g = gs[i]
        dx, dy, s = cur[i]
        r = rng.random()
        if r < 0.45:
            dy += rng.gauss(0, 4)
        elif r < 0.8:
            dx += rng.gauss(0, 3) if g["nome"] != "cabecalho" else 0.0  # cabeçalho centralizado não anda de lado
        else:
            lo, hi = g["escala"]
            s = min(hi, max(lo, s + rng.gauss(0, 0.05)))
        cand = list(cur)
        cand[i] = (dx, dy, s)
        c = cost(cand)
        if c < cur_c or rng.random() < math.exp(-(c - cur_c) / T):
            cur, cur_c = cand, c
            if c < best_c:
                best, best_c = list(cand), c
    end_c, end_detail = cost(best, True)
    if end_c < start_c - 0.05:
        for g, st in zip(gs, best):
            for e, b in zip(g["els"], _apply(g, st)):
                e["b"] = b
                if st[2] > 1.001 and e["papel"] in ("heroi", "produto"):
                    e["_cresceu"] = True
    return {"grupos": len(gs), "custo_antes": round(start_c, 2), "custo_depois": round(min(start_c, end_c), 2),
            "antes": {k: round(v, 2) for k, v in start_detail.items()}, "depois": {k: round(v, 2) for k, v in end_detail.items()},
            "mudancas": {g["nome"]: [round(x, 2) for x in st] for g, st in zip(gs, best) if any(abs(v - d) > 0.3 for v, d in zip(st, (0, 0, 1)))}}
