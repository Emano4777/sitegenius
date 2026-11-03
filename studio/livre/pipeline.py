"""Orquestra a composição livre para QUALQUER briefing (1 produto, vários ou nenhum).

As dependências externas entram como funções (diretor criativo, gerador de cenário, OCR), então o mesmo fluxo roda
com o Gemma da HostGator, com um modelo no Acer ou com dublês nos testes. Mede o tempo de cada etapa.
"""
import base64
import io
import os
import time

from PIL import Image, ImageOps

from studio import composer, htmlflyer

from . import cena, estetica, layout, otimiza, plano, render, valida


def photo_info(data):
    """Qualidade objetiva da foto: recorte limpo e resolução. Foto fraca não vira herói (entra como texto)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    cut, ok = composer.cutout(img)
    w, h = cut.size
    if ok:
        quality = "boa" if min(w, h) >= 180 and max(w, h) >= 300 else "fraca"
    else:
        # fundo não uniforme (foto amadora, cenário): o produto aparece num cartão, nunca some do anúncio
        quality = "cartao" if min(w, h) >= 300 else "fraca"
        cut = cut.convert("RGB")
    cut.thumbnail((900, 900))
    buf = io.BytesIO()
    if quality == "cartao":
        cut.save(buf, "JPEG", quality=88)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    else:
        cut.save(buf, "PNG", optimize=True)
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"aspecto": w / h, "qualidade": quality, "uri": uri}


def _cover(img, size):
    W, H = size
    r = max(W / img.width, H / img.height)
    im = img.convert("RGB").resize((int(img.width * r + 1), int(img.height * r + 1)))
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def _lum_under(img, box):
    W, H = img.size
    x, y, w, h = box
    c = img.crop((int(max(0, x) * W / 100), int(max(0, y) * H / 100), max(1, int(min(100, x + w) * W / 100)), max(1, int(min(100, y + h) * H / 100))))
    px = list(c.convert("L").get_flattened_data()) or [128]
    return sum(px) / len(px)


def run(briefing, photos, director, scene_gen, ocr=None, workdir=None, max_scene=2, max_repair=6, log=None, recent=None):
    """photos: {indice: bytes}. director(messages) -> texto. scene_gen(prompt, tentativa) -> PIL.Image ou None."""
    t_all = time.time()
    tm = {}
    log = log or (lambda *a: None)
    size = htmlflyer.SIZES.get(briefing.get("formato") or "feed_4x5", (1080, 1350))
    info = {}
    t = time.time()
    for i, data in (photos or {}).items():
        try:
            info[i] = photo_info(data)
        except Exception:  # noqa: BLE001 — foto ilegível vira item tipográfico
            info[i] = None
    info = {i: v for i, v in info.items() if v}
    tm["fotos_s"] = round(time.time() - t, 2)
    quality = {i: v["qualidade"] for i, v in info.items()}

    t = time.time()
    raw_text = director(plano.plan_messages(briefing, quality, recent))
    tm["plano_s"] = round(time.time() - t, 1)
    plan = plano.normalize(plano.parse(raw_text), briefing, quality, recent)
    t = time.time()
    solved = layout.solve(plan, briefing, info, size)
    for e in solved["elementos"]:
        if e["tipo"] == "produto" and (info.get(e["produto"]) or {}).get("qualidade") == "cartao":
            e["cartao"] = True
    tm["resolver_s"] = round(time.time() - t, 3)

    # cenário planejado junto: prompt a partir das zonas; confere; corrige ou regera com limite
    scene, scene_report, decisions = None, None, []
    t = time.time()
    official = [plan["titulo"]["txt"], plan["loja"]] + [p["nome"] for p in briefing.get("produtos") or []]
    for attempt in range(1, max_scene + 1):
        prompt = cena.prompt(plan, solved, briefing)
        if attempt > 1:
            prompt += ", absolutamente nenhuma letra ou palavra em nenhuma superfície, paredes e placas lisas"
        img = scene_gen(prompt, attempt)
        if img is None:
            break
        scene = _cover(img, size)
        scene_report = cena.verify(scene, plan, solved, ocr, official)
        d = cena.decide(scene_report, attempt, max_scene)
        decisions.append(d)
        log("cenario", attempt, d, scene_report["problemas"][:4])
        if d == "regerar":
            continue
        if d == "corrigir":
            scene = cena.soften_text(scene, scene_report["textos"])
        break
    tm["cenario_s"] = round(time.time() - t, 1)
    if scene is None:
        scene = Image.new("RGB", size, "#202028")
    if scene_report and decisions and decisions[-1] == "regerar":  # esgotou as tentativas: corrige o que restou
        scene = cena.soften_text(scene, scene_report["textos"])
    plan["_cena_escura"] = _lum_under(scene, (0, 0, 100, 100)) < 110
    buf = io.BytesIO()
    scene.save(buf, "JPEG", quality=90)
    scene_uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    # cor dos nomes pelo brilho medido do cenário embaixo de cada um
    for e in solved["elementos"]:
        if e["tipo"] in ("nome", "info") and not e.get("cor"):
            e["cor"] = "#1e1e22" if _lum_under(scene, e["b"]) > 150 else "#ffffff"
    uris = {i: v["uri"] for i, v in info.items()}

    for e in solved["elementos"]:  # efeito do plano e cor de contorno que contrasta com o preenchimento (para o reparo)
        if e["tipo"] == "texto":
            st = plan["titulo"] if e["papel"] == "titulo" else e
            e["ef_plano"] = st.get("ef")
            e["_reforco_cor"] = "#111114" if htmlflyer._luma(st.get("cor") or "#ffffff") >= 128 else "#ffffff"
    # leitura do cenário: o cabeçalho procura a região mais calma perto do planejado (antes de desenhar)
    t = time.time()
    smap = estetica.scene_map(scene)
    try:  # layout dinâmico: os grupos procuram a melhor solução espacial no cenário real
        moves = otimiza.refine(solved, smap, size, seed=plano._seed(briefing))
    except Exception:  # noqa: BLE001 — sem otimizador, o reposicionamento simples do cabeçalho continua valendo
        moves = estetica.place(solved, smap)
    solved["paleta"] = [plan["titulo"]["cor"], plan["titulo"].get("cor2"), (plan["chamada"] or {}).get("cor"),
                        (plan["chamada"] or {}).get("cor2"), plan["preco"]["bg"], plan["preco"]["fg"],
                        (plan["selo"] or {}).get("cor"), plan["cenario"]["luz"], "#ffffff", "#1e1e22"]
    tm["leitura_cenario_s"] = round(time.time() - t, 2)
    t = time.time()
    history = []
    items = []
    tmp = workdir or os.path.join(os.environ.get("TEMP", "/tmp"), f"sg_livre_{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    for rnd in range(max_repair + 1):
        doc = render.build(plan, solved, briefing, uris, scene_uri, size)
        shot, bare = os.path.join(tmp, "_qa_com.png"), os.path.join(tmp, "_qa_sem.png")
        keys = valida.ink_keys(solved)
        for f in (shot, bare):
            if os.path.exists(f):
                os.remove(f)
        extra = [(render.build(plan, solved, briefing, uris, scene_uri, size, hide_text=True), bare)] if keys else []
        items = valida.measure(doc, size, screenshot=shot if keys else None, extra_shots=extra) or []
        legivel = None
        if items and keys and os.path.exists(shot) and os.path.exists(bare):
            for _ in range(3):  # o Chrome pode ainda estar terminando de gravar a captura
                try:
                    with Image.open(shot) as a, Image.open(bare) as b:
                        legivel = valida.ink(a, b, items, size, keys)
                    break
                except (OSError, ValueError):
                    time.sleep(0.6)
        probs = valida.check(items, solved, size, scene, len(briefing.get("produtos") or []), legivel)
        probs += estetica.check(items, solved, size, smap, plan)
        history.append([list(p) for p in probs])
        if not probs or rnd == max_repair:
            break
        fixed = valida.repair(solved, [p for p in probs if p[0] in valida.CODES], items, size, scene)
        fixed += estetica.repair(solved, [p for p in probs if p[0] not in valida.CODES], items, size, smap, plan)
        if not fixed:
            break
    tm["validacao_s"] = round(time.time() - t, 1)
    png = None
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        png = os.path.join(workdir, "arte.png")
        t = time.time()
        doc = render.build(plan, solved, briefing, uris, scene_uri, size)
        items = valida.measure(doc, size, screenshot=png) or items
        open(os.path.join(workdir, "arte.html"), "w", encoding="utf-8").write(doc)
        scene.save(os.path.join(workdir, "cenario.jpg"), quality=90)
        tm["render_final_s"] = round(time.time() - t, 1)
    tm["total_s"] = round(time.time() - t_all, 1)
    return {"plano": plan, "geometria": solved, "prompt_cenario": cena.prompt(plan, solved, briefing),
            "cenario": scene_report, "decisoes_cenario": decisions, "problemas_por_rodada": history,
            "problemas_finais": history[-1] if history else [], "metricas": valida.metrics(items, size, len(briefing.get("produtos") or [])),
            "tempos": tm, "png": png, "plano_bruto": raw_text, "posicionamento_cenario": moves,
            "avaliacao_estetica": valida.aesthetic_hook(png, plan)}
