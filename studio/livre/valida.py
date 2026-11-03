"""Validação técnica objetiva (sem notas subjetivas de modelo) e reparos que preservam a hierarquia.

Mede no Chromium a peça montada: elemento fora da peça, texto cortado, preço/texto escondido, textos encavalados,
contraste real contra o cenário, fontes pequenas demais e hierarquia fraca (preço principal/herói pequenos).
Reparo: primeiro MOVE para o espaço livre mais próximo; se não houver, reduz o elemento SECUNDÁRIO de menor
prioridade até o piso dele; preço principal, herói e título nunca são reduzidos para caber.
Há um gancho (aesthetic_hook) para uma avaliação estética local futura, que não aprova nem reprova sozinha.
"""
import html as _html
import json
import os
import subprocess
import tempfile

from studio import htmlflyer

from .layout import PROTECTED, overlap

CHROME_CANDIDATES = [os.environ.get("SG_CHROME") or "", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                     "/usr/bin/chromium", "/usr/bin/google-chrome"]
MIN_FONT = {"nome": 20, "nome_heroi": 30, "preco": 44, "preco_principal": 90, "titulo": 60, "chamada": 26, "loja": 20}
# pisos medidos (% da peça: 1 produto, vários); o resolvedor mira acima deles (6,0/3,8 e 16/9,5)
MIN_AREA = {"preco_principal": (4.6, 3.0), "heroi": (14.0, 7.5)}
LEGIBLE_INK = 0.45  # fração mínima da tinta do texto com contraste >= 3:1 contra o fundo (WCAG, texto grande)


def chrome():
    return next((c for c in CHROME_CANDIDATES if c and os.path.exists(c)), None)


def measure(doc, size, screenshot=None, extra_shots=()):
    """Abre a peça no Chromium (janela maior que a peça, para medir a borda) e devolve as medições.
    screenshot: captura da peça; extra_shots: [(outro_doc, caminho)] capturados AO MESMO TEMPO (ex.: a peça sem textos)."""
    exe = chrome()
    if not exe:
        return None
    flags = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=8000"]
    with tempfile.TemporaryDirectory() as d:
        urls = []
        for i, html_doc in enumerate([doc] + [x[0] for x in extra_shots]):
            path = os.path.join(d, f"peca{i}.html")
            open(path, "w", encoding="utf-8").write(html_doc)
            urls.append("file:///" + path.replace("\\", "/"))
        procs = [subprocess.Popen(flags + [f"--window-size={size[0] + 160},{size[1] + 210}", f"--user-data-dir={d}/u0", "--dump-dom", urls[0]],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)]
        shots = ([(urls[0], screenshot)] if screenshot else []) + [(u, x[1]) for u, x in zip(urls[1:], extra_shots)]
        for j, (u, target) in enumerate(shots):
            procs.append(subprocess.Popen(flags + [f"--window-size={size[0]},{size[1]}", f"--user-data-dir={d}/u{j + 1}",
                                                   f"--screenshot={target}", u], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        out = procs[0].communicate(timeout=120)[0].decode("utf-8", "replace")
        for pr in procs[1:]:
            pr.wait(timeout=120)
    a = out.find('<pre id="qa"')
    if a < 0:
        return []
    a = out.find(">", a) + 1
    return json.loads(_html.unescape(out[a:out.find("</pre>", a)]))


def _contrast(hex_color, lum):
    def rel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    h = htmlflyer._hex(hex_color, "#ffffff")
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    l1 = 0.2126 * rel(r) + 0.7152 * rel(g) + 0.0722 * rel(b)
    l2 = rel(lum)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def ink(full, bare, items, size, keys):
    """Legibilidade pelo DESENHO real: pixels que mudam entre a peça com e sem textos são o texto (preenchimento,
    contorno, brilho, placa). Fração do miolo das letras com contraste >= 3:1 contra o entorno renderizado. Vale para
    qualquer efeito (arco, neon, metal, contorno, gradiente) e qualquer cenário."""
    import numpy as np  # noqa: PLC0415

    def rel(img):
        a = np.asarray(img.convert("RGB").resize(size), dtype=np.float32) / 255
        a = np.where(a <= 0.03928, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        return a @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), a
    lf, af = rel(full)
    lb, ab = rel(bare)
    out = {}
    for it in items:
        if it["k"] not in keys:
            continue
        x0, y0 = max(0, int(it["x"])), max(0, int(it["y"]))
        x1, y1 = min(size[0], int(it["x"] + it["w"])), min(size[1], int(it["y"] + it["h"]))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        diff = np.abs(af[y0:y1, x0:x1] - ab[y0:y1, x0:x1]).sum(axis=2)
        mask = diff > 0.12
        if mask.sum() < 50:
            out[it["k"]] = 0.0
            continue
        # miolo da letra = pixels que mais mudaram; entorno = o resto da área COMO APARECE na peça (brilho, placa,
        # contorno e cenário). Legível quando o miolo se separa do entorno.
        core = mask & (diff >= np.percentile(diff[mask], 60))
        region = lf[y0:y1, x0:x1]
        around = region[~core]
        if around.size < 50:
            around = lb[y0:y1, x0:x1].ravel()
        s = float(np.median(around))
        a = region[core]
        ratio = (np.maximum(a, s) + 0.05) / (np.minimum(a, s) + 0.05)
        out[it["k"]] = round(float((ratio >= 3.0).mean()), 2)
    return out


def _ef(e):
    """Efeito em uso no elemento de texto (o reparo pode ter trocado o do plano)."""
    return e.get("ef_forcado") or e.get("ef_plano") or e.get("ef")


def ink_keys(solved):
    """Títulos (qualquer efeito) e todo texto em arco: os que a conferência por cor não cobre."""
    return {f'{e["papel"]}:{e.get("produto", "")}' for e in solved["elementos"]
            if e["tipo"] == "texto" and (e["papel"] == "titulo" or _ef(e) == "arco")}


def check(items, solved, size, bg_img=None, n_products=0, legivel=None):
    """Lista de problemas objetivos: (código, chave, detalhe). legivel: {chave: fração de tinta legível} de ink()."""
    W, H = size
    by = {f'{e["papel"]}:{e.get("produto", "")}': e for e in solved["elementos"]}
    probs = []
    for k, frac in (legivel or {}).items():
        if frac < LEGIBLE_INK:
            probs.append(("legibilidade", k, frac))
    for it in items:
        k = it["k"]; papel = k.split(":")[0]
        if it.get("arco") and not 0.82 <= it.get("arco_escala", 1) <= 1.25:
            probs.append(("letras_deformadas", k, round(it["arco_escala"], 2)))
        if it.get("fora"):
            dx = (-it["x"] if it["x"] < 0 else W - (it["x"] + it["w"]) if it["x"] + it["w"] > W else 0) * 100 / W
            dy = (-it["y"] if it["y"] < 0 else H - (it["y"] + it["h"]) if it["y"] + it["h"] > H else 0) * 100 / H
            probs.append(("fora_da_peca", k, (round(dx, 2), round(dy, 2))))
        if it.get("corta"):
            probs.append(("texto_cortado", k, None))
        if papel not in ("produto", "heroi", "icone") and it.get("visivel", 1) < 0.75:
            probs.append(("escondido", k, round(it.get("visivel", 0), 2)))
        need_font = MIN_FONT[papel] * (0.78 if papel == "preco_principal" and n_products > 1 else 1.0) if papel in MIN_FONT else 0
        if need_font and it.get("fonte") and it["fonte"] < need_font * W / 1080:  # no arco, a altura real da letra
            probs.append(("fonte_pequena", k, round(it["fonte"])))
        if papel in MIN_AREA:
            area = it["w"] * it["h"] * 100 / (W * H) * 100 / 100
            need = MIN_AREA[papel][0 if n_products == 1 else 1]
            if area < need:
                probs.append(("hierarquia_fraca", k, round(area, 1)))
        el = by.get(k)
        if bg_img is not None and el and el["tipo"] in ("nome", "texto", "info"):
            col = el.get("cor") or el.get("_cor_plano") or "#ffffff"
            if el["papel"] == "titulo":
                continue  # o título leva neon/metal/contorno/sombra fortes; chamada e empresa são conferidas
            crop = bg_img.crop((max(0, int(it["x"])), max(0, int(it["y"])), max(1, int(it["x"] + it["w"])), max(1, int(it["y"] + it["h"])))).convert("L")
            px = sorted(crop.get_flattened_data())
            if px and not el.get("placa"):
                worst = min(_contrast(col, px[len(px) // 10]), _contrast(col, px[len(px) * 9 // 10]))
                if worst < 2.5:
                    probs.append(("contraste", k, round(worst, 2)))
    texty = [i for i in items if i["k"].split(":")[0] in ("titulo", "loja", "chamada", "nome", "nome_heroi", "preco", "preco_principal", "lista", "info", "selo")]
    for a in range(len(texty)):
        for b in range(a + 1, len(texty)):
            A, B = texty[a], texty[b]
            same = A["k"].split(":")[1] and A["k"].split(":")[1] == B["k"].split(":")[1]
            if same:
                continue
            ox, oy = overlap([A["x"], A["y"], A["w"], A["h"]], [B["x"], B["y"], B["w"], B["h"]])
            if ox * oy > 0.08 * min(A["w"] * A["h"], B["w"] * B["h"]):
                probs.append(("sobreposicao", A["k"], B["k"]))
    return probs


def repair(solved, probs, items, size):
    """Aplica reparos que preservam a hierarquia. Devolve quantos reparos foram feitos."""
    W, H = size
    by = {f'{e["papel"]}:{e.get("produto", "")}': e for e in solved["elementos"]}
    rects = {i["k"]: [i["x"] * 100 / W, i["y"] * 100 / H, i["w"] * 100 / W, i["h"] * 100 / H] for i in items}
    done = 0
    for code, k, det in probs:
        e = by.get(k)
        if code == "fora_da_peca" and e:
            dx, dy = det
            e["b"][0] += dx + (0.8 if dx > 0 else -0.8 if dx < 0 else 0)
            e["b"][1] += dy + (0.8 if dy > 0 else -0.8 if dy < 0 else 0)
            done += 1
        elif code == "escondido" and e:
            e["z"] = max(x["z"] for x in solved["elementos"]) + 2
            done += 1
        elif code in ("letras_deformadas", "fonte_pequena") and e and e["tipo"] == "texto" \
                and _ef(e) == "arco" and not e.get("_saiu_do_arco"):
            # texto longo demais para o arco: em linha reta ele cabe maior e sem deformar (vale para qualquer título)
            e["ef_forcado"] = "adesivo"; e["_saiu_do_arco"] = True
            done += 1
        elif code == "legibilidade" and e:
            if not e.get("reforco"):
                e["reforco"] = e.get("_reforco_cor") or "#111114"  # contorno largo que separa a letra do fundo
                done += 1
            elif not e.get("placa"):
                if _ef(e) == "arco":
                    e["ef_forcado"] = "adesivo"; e["_saiu_do_arco"] = True
                e["placa"] = True
                done += 1
        elif code == "contraste" and e:
            if e.get("_tentou_cor"):
                e["placa"] = True; e["cor"] = "#ffffff"
            else:
                e["cor"] = "#ffffff" if htmlflyer._luma(e.get("cor") or "#000000") < 128 else "#1e1e22"; e["_tentou_cor"] = True
            done += 1
        elif code == "sobreposicao":
            a, b = by.get(k), by.get(det)
            if not a or not b:
                continue
            mover = a if a["prio"] > b["prio"] else b
            if mover["papel"] in PROTECTED:
                mover = b if mover is a else a
                if mover["papel"] in PROTECTED:
                    continue
            if _move_to_free(mover, solved, rects, size) or _shrink(mover):
                done += 1
        elif code in ("texto_cortado", "fonte_pequena") and e and e["papel"] not in PROTECTED:
            # dá mais largura se houver espaço livre ao lado; senão aceita o piso
            grow = min(e["b"][2] * 0.15, 100 - 4 - (e["b"][0] + e["b"][2]))
            if grow > 0.5:
                e["b"][2] += grow; done += 1
    return done


def _move_to_free(e, solved, rects, size, step=1.5):
    """Procura, em espiral, a posição livre mais próxima. Usa o desenho MEDIDO no navegador (sombra, inclinação e
    efeitos aumentam o desenho além da caixa) e aplica o mesmo deslocamento na caixa."""
    k_ = size[0] / size[1]
    key = f'{e["papel"]}:{e.get("produto", "")}'
    x, y, w, h = rects.get(key, e["b"])
    others = [rects.get(f'{o["papel"]}:{o.get("produto", "")}', o["b"]) for o in solved["elementos"]
              if o is not e and not (o.get("produto") is not None and o.get("produto") == e.get("produto")) and o["tipo"] not in ("icone",)]
    others += [z for z in solved.get("zonas_objetos") or []] if e["tipo"] in ("produto", "preco", "nome", "lista") else []
    for r in range(1, 40):
        for dx, dy in ((0, r), (0, -r), (r, 0), (-r, 0), (r, r), (-r, r), (r, -r), (-r, -r)):
            nx, ny = x + dx * step, y + dy * step * k_
            if nx < 2 or ny < 2 or nx + w > 98 or ny + h > 98:
                continue
            if all(min(nx + w, o[0] + o[2]) - max(nx, o[0]) <= 0.3 or min(ny + h, o[1] + o[3]) - max(ny, o[1]) <= 0.3 for o in others):
                e["b"][0] += nx - x
                e["b"][1] += ny - y
                return True
    return False


def _shrink(e):
    s = e.get("_s", 1.0)
    if e["papel"] in PROTECTED or s * 0.92 < e["min"]:
        return False
    e["_s"] = s * 0.92
    cx, cy = e["b"][0] + e["b"][2] / 2, e["b"][1] + e["b"][3] / 2
    e["b"][2] *= 0.92; e["b"][3] *= 0.92
    e["b"][0], e["b"][1] = cx - e["b"][2] / 2, cy - e["b"][3] / 2
    return True


def metrics(items, size, n_products):
    """Indicadores objetivos de hierarquia (para relatório e para comparação entre versões)."""
    W, H = size
    get = lambda papel: [i for i in items if i["k"].split(":")[0] == papel]  # noqa: E731
    area = lambda i: round(i["w"] * i["h"] * 100 / (W * H), 1)  # noqa: E731
    out = {}
    for papel in ("preco_principal", "heroi", "titulo"):
        lst = get(papel)
        if lst:
            out[f"area_{papel}"] = area(lst[0]); out[f"fonte_{papel}"] = round(lst[0].get("fonte") or 0)
    others = get("produto")
    if others and get("heroi"):
        out["heroi_vs_demais"] = round(area(get("heroi")[0]) / max(0.1, max(area(o) for o in others)), 2)
    return out


def aesthetic_hook(png_path, plan):
    """Lugar reservado para uma avaliação estética LOCAL futura (modelo de visão validado). Hoje não opina: a aprovação
    é só pela validação objetiva. Quando houver um avaliador confiável, ele só sugere ajustes, não aprova sozinho."""
    return None
