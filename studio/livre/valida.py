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


def chrome():
    return next((c for c in CHROME_CANDIDATES if c and os.path.exists(c)), None)


def measure(doc, size, screenshot=None):
    """Abre a peça no Chromium (janela maior que a peça, para medir a borda) e devolve as medições."""
    exe = chrome()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "peca.html")
        open(path, "w", encoding="utf-8").write(doc)
        url = "file:///" + path.replace("\\", "/")
        r = subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={size[0] + 160},{size[1] + 210}",
                            "--virtual-time-budget=8000", "--dump-dom", url], capture_output=True, timeout=120)
        out = r.stdout.decode("utf-8", "replace")
        if screenshot:
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={size[0]},{size[1]}",
                            "--virtual-time-budget=8000", f"--screenshot={screenshot}", url], capture_output=True, timeout=120)
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


def check(items, solved, size, bg_img=None, n_products=0):
    """Lista de problemas objetivos: (código, chave, detalhe)."""
    W, H = size
    by = {f'{e["papel"]}:{e.get("produto", "")}': e for e in solved["elementos"]}
    probs = []
    for it in items:
        k = it["k"]; papel = k.split(":")[0]
        if it.get("fora"):
            dx = (-it["x"] if it["x"] < 0 else W - (it["x"] + it["w"]) if it["x"] + it["w"] > W else 0) * 100 / W
            dy = (-it["y"] if it["y"] < 0 else H - (it["y"] + it["h"]) if it["y"] + it["h"] > H else 0) * 100 / H
            probs.append(("fora_da_peca", k, (round(dx, 2), round(dy, 2))))
        if it.get("corta"):
            probs.append(("texto_cortado", k, None))
        if papel not in ("produto", "heroi", "icone") and it.get("visivel", 1) < 0.75:
            probs.append(("escondido", k, round(it.get("visivel", 0), 2)))
        if papel in MIN_FONT and it.get("fonte") and it["fonte"] < MIN_FONT[papel] * W / 1080:
            probs.append(("fonte_pequena", k, round(it["fonte"])))
        if papel in MIN_AREA:
            area = it["w"] * it["h"] * 100 / (W * H) * 100 / 100
            need = MIN_AREA[papel][0 if n_products == 1 else 1]
            if area < need:
                probs.append(("hierarquia_fraca", k, round(area, 1)))
        el = by.get(k)
        if bg_img is not None and el and el["tipo"] in ("nome", "texto", "info") and el.get("cor", el.get("cor")) is not None:
            col = el.get("cor") or "#ffffff"
            if el["papel"] in ("titulo", "chamada"):
                continue  # títulos/chamadas levam sombra, neon ou contorno próprios
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
    """Procura, em espiral, a posição livre mais próxima (sem colidir com outros textos/produtos de outros itens)."""
    k_ = size[0] / size[1]
    x, y, w, h = e["b"]
    others = [o["b"] for o in solved["elementos"] if o is not e and not (o.get("produto") is not None and o.get("produto") == e.get("produto"))
              and o["tipo"] not in ("icone",)]
    others += [z for z in solved.get("zonas_objetos") or []] if e["tipo"] in ("produto", "preco", "nome", "lista") else []
    best = None
    for r in range(1, 40):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, r), (r, -r), (-r, -r)):
            nx, ny = x + dx * step, y + dy * step * k_
            if nx < 3 or ny < 3 or nx + w > 97 or ny + h > 97:
                continue
            if all(min(nx + w, o[0] + o[2]) - max(nx, o[0]) <= 0.3 or min(ny + h, o[1] + o[3]) - max(ny, o[1]) <= 0.3 for o in others):
                best = (nx, ny)
                break
        if best:
            break
    if best:
        e["b"][0], e["b"][1] = best
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
