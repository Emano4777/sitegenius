"""Bateria comparativa: arquitetura atual (Gemma + Z-Image + compositor) x FLUX.2 Klein (A: arte completa; B: híbrida).
  python klein_cmp.py planos     -> o Gemma interpreta os 10 pedidos (um plano por pedido, usado pelas três versões)
  python klein_cmp.py fila       -> grava as solicitações do Klein (A e B) na fila do executor do Acer
  python klein_cmp.py atual      -> arquitetura atual: cenário Z-Image (Acer, pelo chat de teste) + compositor
  python klein_cmp.py compor     -> B: compositor sobre o cenário do Klein (ampliado no Acer); A: arte do Klein como veio
  python klein_cmp.py folha      -> folhas lado a lado + métricas"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(HERE)
REPO = r"C:\Users\windows\Downloads\sitegenius-rebuild"
sys.path.insert(0, REPO)
sys.path.insert(0, SP)
sys.path.insert(0, os.path.join(SP, "poc_free"))
from PIL import Image  # noqa: E402

from studio.livre import cena, formato, layout, pipeline, plano  # noqa: E402

FILA = os.path.join(REPO, "deploy", "acer_klein_lab", "fila")
PLANS = os.path.join(HERE, "planos.json")


def _photos(dirname, files):
    return {i: open(os.path.join(SP, dirname, f), "rb").read() for i, f in enumerate(files)}


def cases():
    import livre_real as L  # noqa: PLC0415
    m5, f5 = L.mercado_photos([0, 1, 2, 3, 4])
    fp, ff = L.farm_photos()
    return {
        "01_blackfriday_tech": ("Crie uma arte de Black Friday para minha empresa de tecnologia, a TechPoint, bem chamativa, com promoções e "
                                "elementos modernos. Fone Bluetooth de R$ 399 por R$ 299 e Smartwatch por R$ 499. Story 1080 x 1920.",
                                {"loja": "TechPoint", "titulo": "Black Friday", "segmento": "loja de tecnologia",
                                 "produtos": [{"nome": "Fone Bluetooth", "preco": "R$ 299,00", "preco_de": "R$ 399,00"},
                                              {"nome": "Smartwatch", "preco": "R$ 499,00"}]}, {}),
        "02_supermercado": ("Faz um encarte de ofertas da semana do Mercado Bom Preço com estes produtos e preços, para o feed do Instagram "
                            "em 1080 x 1350.", {"loja": "Mercado Bom Preço", "titulo": "Ofertas da Semana", "segmento": "supermercado",
                                                 "produtos": m5}, f5),
        "03_servicos_ti": ("Banner horizontal de 1920 x 1080 divulgando os serviços da TechPoint Assistência: formatação, remoção de vírus e "
                           "montagem de computadores. WhatsApp (11) 98888-7777.",
                           {"loja": "TechPoint Assistência", "titulo": "", "segmento": "serviços de tecnologia",
                            "produtos": [{"nome": "Formatação", "preco": "R$ 120,00"}, {"nome": "Remoção de vírus", "preco": "R$ 90,00"},
                                         {"nome": "Montagem de PC", "preco": "R$ 250,00"}], "info": {"whatsapp": "(11) 98888-7777"}}, {}),
        "04_imobiliaria": ("Anúncio quadrado 1080 x 1080 da Lar Ideal Imóveis: apartamento de 2 quartos, 62 m², na Vila Nova, por R$ 1.800/mês. "
                           "CRECI 12345-J.", {"loja": "Lar Ideal Imóveis", "titulo": "", "segmento": "imobiliária",
                                              "produtos": [{"nome": "Apartamento 2 quartos, 62 m², Vila Nova", "preco": "R$ 1.800/mês"}],
                                              "info": {"creci": "12345-J"}}, {}),
        "05_estetica": ("Post para o feed da Clínica Bella Pele, de estética: limpeza de pele por R$ 149 e drenagem linfática por R$ 120. "
                        "Quero algo elegante.", {"loja": "Clínica Bella Pele", "titulo": "", "segmento": "clínica de estética",
                                                  "produtos": [{"nome": "Limpeza de pele", "preco": "R$ 149,00"},
                                                               {"nome": "Drenagem linfática", "preco": "R$ 120,00"}]}, {}),
        "06_farmacia_fotos": ("Story da Drogaria Saúde+ com as ofertas destes produtos, usando as fotos que mandei.",
                              {"loja": "Drogaria Saúde+", "titulo": "Ofertas de Cuidado", "segmento": "farmácia", "produtos": fp}, ff),
        "07_restaurante": ("Promoção do restaurante Sabor da Casa: prato executivo por R$ 29,90 de segunda a sexta, no almoço. Feed 1080 x 1350.",
                           {"loja": "Sabor da Casa", "titulo": "", "segmento": "restaurante",
                            "produtos": [{"nome": "Prato executivo", "preco": "R$ 29,90", "oferta": "de segunda a sexta, no almoço"}]}, {}),
        "08_minimalista": ("Arte minimalista e sofisticada para a joalheria Aurum anunciando a nova coleção. Sem preços. 1080 x 1350.",
                           {"loja": "Aurum Joias", "titulo": "Nova Coleção", "segmento": "joalheria", "produtos": []}, {}),
        "09_futurista_3d": ("Arte futurista com efeitos 3D para o lançamento do aplicativo FinX, de finanças pessoais. Quero em 4K, horizontal.",
                            {"loja": "FinX", "titulo": "Chegou o FinX", "segmento": "aplicativo de finanças", "produtos": []}, {}),
        "10_aberta": ("Faz uma arte bonita pro meu petshop, o Patas & Cia.",
                      {"loja": "Patas & Cia", "titulo": "", "segmento": "pet shop", "produtos": []}, {}),
    }


def briefing_of(name):
    text, b, photos = cases()[name]
    w, h, why = formato.parse(text)
    prods = [{k: p.get(k) for k in ("nome", "preco", "preco_de", "oferta") if p.get(k)} for p in b.get("produtos") or []]
    return {**b, "produtos": prods, "pedido": text, "tamanho": (w, h), "formato": "feed_4x5"}, photos, why


def cmd_planos():
    import poc_run  # noqa: PLC0415
    out = json.load(open(PLANS, encoding="utf-8")) if os.path.exists(PLANS) else {}
    for name in cases():
        if name in out:
            continue
        b, photos, why = briefing_of(name)
        q = {i: pipeline.photo_info(d)["qualidade"] for i, d in photos.items()}
        t = time.time()
        r = poc_run.remote_chat(plano.plan_messages(b, q), num_predict=1100, temperature=0.7)
        out[name] = {"texto": r["conteudo"], "s": r["s"], "tamanho": b["tamanho"], "motivo_tamanho": why}
        print(name, r["s"], b["tamanho"], flush=True)
        json.dump(out, open(PLANS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _solved(name, raw):
    b, photos, _ = briefing_of(name)
    info = {i: pipeline.photo_info(d) for i, d in photos.items()}
    q = {i: v["qualidade"] for i, v in info.items()}
    plan = plano.normalize(plano.parse(raw), b, q)
    return b, plan, layout.solve(plan, b, info, tuple(b["tamanho"]))


def cmd_fila():
    plans = json.load(open(PLANS, encoding="utf-8"))
    os.makedirs(os.path.join(FILA, "entrada"), exist_ok=True)
    for name, p in plans.items():
        b, plan, solved = _solved(name, p["texto"])
        gen = formato.generation_plan(tuple(b["tamanho"]))
        prompts = {"B": cena.prompt(plan, solved, b), "A": cena.full_prompt(plan, b)}
        for ab, pr in prompts.items():
            job = {"id": f"{name}_{ab}", "abordagem": ab, "prompt": pr, "gen_w": gen["geracao"][0], "gen_h": gen["geracao"][1],
                   "final_w": gen["final"][0], "final_h": gen["final"][1], "fator": gen["fator"], "seed": 1000 + len(name) * 37}
            json.dump(job, open(os.path.join(FILA, "entrada", job["id"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(name, gen)


def cmd_atual():
    from livre_real import Chat  # noqa: PLC0415
    plans = json.load(open(PLANS, encoding="utf-8"))
    chat, ocr = Chat(), cena.rapidocr_fn()
    res_p = os.path.join(HERE, "atual", "resumo.json")
    os.makedirs(os.path.dirname(res_p), exist_ok=True)
    res = json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {}
    for name, p in plans.items():
        if name in res:
            continue
        b, photos, _ = briefing_of(name)
        times = []

        def gen(prompt, attempt):
            img, t = chat.image(prompt)
            times.append(t)
            if img is not None:
                times.append(list(img.size))
            return img
        t0 = time.time()
        r = pipeline.run(b, photos, lambda m: p["texto"], gen, ocr=ocr, workdir=os.path.join(HERE, "atual", name), max_scene=2)
        res[name] = {"finais": [x[0] for x in r["problemas_finais"]], "cenario": times, "tempos": r["tempos"], "total_s": round(time.time() - t0, 1)}
        print(name, res[name]["finais"], times, flush=True)
        json.dump(res, open(res_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_compor():
    plans = json.load(open(PLANS, encoding="utf-8"))
    ocr = cena.rapidocr_fn()
    out = {}
    for name, p in plans.items():
        b, photos, _ = briefing_of(name)
        W, H = b["tamanho"]
        d = os.path.join(FILA, "saida")
        res = {}
        for ab in ("A", "B"):
            jd = os.path.join(d, f"{name}_{ab}")
            if not os.path.exists(os.path.join(jd, "metricas.json")):
                continue
            m = json.load(open(os.path.join(jd, "metricas.json"), encoding="utf-8"))
            src = os.path.join(jd, "ampliada.png") if os.path.exists(os.path.join(jd, "ampliada.png")) else os.path.join(jd, "imagem.png")
            if not os.path.exists(src):
                res[ab] = {"metricas": m}
                continue
            img = Image.open(src).convert("RGB")
            od = os.path.join(HERE, f"klein_{ab}", name)
            os.makedirs(od, exist_ok=True)
            if ab == "A":  # geração completa: só ajusta ao tamanho exato pedido (sem esticar: cobre e recorta)
                final = pipeline._cover(img, (W, H))
                final.save(os.path.join(od, "arte.png"))
                res[ab] = {"metricas": m, "fidelidade": text_fidelity(final, b, plano.normalize(plano.parse(p["texto"]), b, {}), ocr)}
            else:
                t = time.time()
                r = pipeline.run(b, photos, lambda mm: p["texto"], lambda pr, a: img, ocr=ocr, workdir=od, max_scene=1)
                res[ab] = {"metricas": m, "finais": [x[0] for x in r["problemas_finais"]], "compor_s": round(time.time() - t, 1),
                           "decisao_cenario": r["decisoes_cenario"]}
        out[name] = res
        print(name, {k: (v.get("finais"), v.get("fidelidade", {}).get("taxa")) for k, v in res.items()}, flush=True)
    json.dump(out, open(os.path.join(HERE, "klein_resumo.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def text_fidelity(img, b, plan, ocr):
    """Abordagem A: quantos textos obrigatórios aparecem EXATAMENTE (OCR), inclusive preços."""
    import re  # noqa: PLC0415
    import unicodedata  # noqa: PLC0415
    read = " ".join(t[4] for t in (ocr(img) or []))

    def n(s):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip().lower()
    req = [plan["titulo"]["txt"], plan.get("loja") or ""] + [x for p in b.get("produtos") or [] for x in (p["nome"], p.get("preco") or "")]
    req = [r for r in req if r]
    hits = [r for r in req if n(r) in n(read) or n(r).replace(" ", "") in n(read).replace(" ", "")]
    return {"obrigatorios": len(req), "exatos": len(hits), "taxa": round(len(hits) / max(1, len(req)), 2), "faltaram": [r for r in req if r not in hits][:8]}


def cmd_folha():
    from PIL import ImageDraw, ImageFont  # noqa: PLC0415
    names = list(cases())
    font = ImageFont.truetype("arial.ttf", 20)
    cols = [("atual", "Atual (Z-Image + compositor)"), ("klein_A", "Klein A (arte completa)"), ("klein_B", "Klein B (híbrido)")]
    tw = 330
    for part, chunk in enumerate([names[:5], names[5:]]):
        rows = []
        for n in chunk:
            ims = []
            for c, _ in cols:
                p = os.path.join(HERE, c, n, "arte.png")
                ims.append(Image.open(p).convert("RGB") if os.path.exists(p) else None)
            ar = next((im.height / im.width for im in ims if im), 1.25)
            rows.append((n, ims, int(tw * ar)))
        H = sum(h + 12 for _, _, h in rows) + 40
        sheet = Image.new("RGB", (190 + len(cols) * (tw + 10), H), "white")
        dr = ImageDraw.Draw(sheet)
        for k, (_, lab) in enumerate(cols):
            dr.text((190 + k * (tw + 10), 10), lab, fill="black", font=font)
        y = 40
        for n, ims, h in rows:
            dr.text((8, y + h // 2 - 10), n, fill="black", font=font)
            for k, im in enumerate(ims):
                if im:
                    im.thumbnail((tw, h))
                    sheet.paste(im, (190 + k * (tw + 10), y))
            y += h + 12
        sheet.save(os.path.join(HERE, f"comparacao_klein_{part + 1}.jpg"), quality=86)


if __name__ == "__main__":
    {"planos": cmd_planos, "fila": cmd_fila, "atual": cmd_atual, "compor": cmd_compor, "folha": cmd_folha}[sys.argv[1]]()
