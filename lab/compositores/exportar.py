"""Exporta a composição FINAL do nosso pipeline (mesma análise visual e mesmas decisões) num formato neutro, para que
outros compositores (Pango/Cairo, LayerStyle) desenhem exatamente o mesmo plano sobre a mesma imagem."""
import base64
import json
import os
import sys

sys.path.insert(0, r"C:\Users\windows\Downloads\sitegenius-rebuild")
from studio.livre import efeitos, plano, recursos  # noqa: E402
from studio.livre.render import _split_price  # noqa: E402


def font_file(key):
    if str(key).startswith("g:"):
        from studio.livre import fontes_google  # noqa: PLC0415
        p, _ = fontes_google.ensure(key[2:])
        return p, key[2:]
    return recursos.font_path(key), recursos.FONTS.get(key, recursos.FONTS["anton"])[1]


def export(result, briefing, photos_info, scene_path, out_dir):
    """result = pipeline.run(...); photos_info = {i: pipeline.photo_info(...)}."""
    os.makedirs(out_dir, exist_ok=True)
    pl, sol = result["plano"], result["geometria"]
    W, H = briefing.get("tamanho") or (1080, 1350)
    base = result.get("base_sem_texto")
    composed = bool(base and os.path.exists(base))  # base já tem cenário, fotos, ícones e formas dos selos
    med = {i["k"]: [i["x"] * 100 / W, i["y"] * 100 / H, i["w"] * 100 / W, i["h"] * 100 / H] for i in result.get("medidas") or []}
    prods = briefing.get("produtos") or []
    seed = plano._seed(briefing)
    els = []
    for e in sorted(sol["elementos"], key=lambda x: x["z"]):
        t = e["tipo"]
        key = f'{e["papel"]}:{e.get("produto", "")}'
        # texto no espaço que a validação MEDIU e aprovou (o outro renderizador não pode encavalar nada)
        box = med.get(key) if composed and t in ("texto", "nome", "info", "preco") and key in med else e["b"]
        d = {"tipo": t, "papel": e["papel"], "b": box, "z": e["z"]}
        if t == "texto":
            st = pl["titulo"] if e["papel"] == "titulo" else e
            txt = {"titulo": pl["titulo"]["txt"], "loja": pl["loja"], "chamada": (pl["chamada"] or {}).get("txt", "")}[e["papel"]]
            if recursos.FONTS.get(st.get("fonte"), ("", "", "", False))[3]:
                txt = txt.upper()
            cor = (e.get("cor_forcada") if e["papel"] == "titulo" else None) or st.get("cor") or "#ffffff"
            ef = e.get("ef_forcado") or st.get("ef") or "sombra"
            fx = st.get("efx") if not e.get("ef_forcado") and st.get("efx") else efeitos.preset(ef, cor, st.get("cor2") or "#111111", seed)
            if e.get("reforco"):
                fx = {**fx, "contornos": fx["contornos"] + [{"espessura": 0.04, "cor": e["reforco"]}]}
            f, fam = font_file(st.get("fonte") or "anton")
            d.update({"texto": txt, "fonte": f, "familia": fam, "peso": st.get("peso"), "cor": cor, "efeito": fx, "arco": ef == "arco",
                      "halo": bool(e.get("placa"))})
        elif t in ("nome", "info"):
            txt = prods[e["produto"]]["nome"] if t == "nome" else "\n".join(e.get("linhas") or [])
            f, fam = font_file(pl["nomes"]["fonte"] if t == "nome" else "poppins")
            d.update({"texto": txt, "fonte": f, "familia": fam, "cor": e.get("cor") or pl["nomes"]["cor"], "halo": bool(e.get("placa"))})
        elif t == "preco":
            p = prods[e["produto"]]
            whole, cents, suffix = _split_price(p.get("preco") or "")
            f, fam = font_file(pl["preco"]["fonte"])
            d.update({"inteiro": whole, "centavos": cents, "sufixo": suffix, "de": p.get("preco_de") or "", "fonte": f, "familia": fam,
                      "estilo": pl["preco"]["estilo"], "bg": pl["preco"]["bg"], "fg": pl["preco"]["fg"], "principal": e["papel"] == "preco_principal"})
        elif t == "produto" and composed:
            continue  # a foto já está na base (com sombra e luz da cena)
        elif t == "produto":
            info = photos_info[e["produto"]]
            path = os.path.join(out_dir, f"produto{e['produto']}.png")
            open(path, "wb").write(base64.b64decode(info["uri"].split(",", 1)[1]))
            d.update({"foto": path, "cartao": bool(e.get("cartao"))})
        elif t == "lista":
            f, fam = font_file("poppins")
            fp, famp = font_file(pl["preco"]["fonte"])
            d.update({"linhas": [[prods[i]["nome"], prods[i].get("preco") or ""] for i in e["itens"]], "fonte": f, "familia": fam,
                      "fonte_preco": fp, "familia_preco": famp, "bg": pl["preco"]["bg"], "fg": pl["preco"]["fg"]})
        elif t == "selo":
            f, fam = font_file(pl["preco"]["fonte"])
            d.update({"texto": e["txt"].upper(), "forma": e["forma"], "cor": e["cor"], "cor2": e["cor2"], "giro": e.get("giro", -8),
                      "fonte": f, "familia": fam, "so_texto": composed})
        elif t == "icone":
            continue  # ícones SVG: só no nosso compositor (os outros não têm o conjunto de ícones)
        els.append(d)
    spec = {"tamanho": [W, H], "cenario": base if composed else scene_path, "base_composta": composed, "elementos": els,
            "luz": pl["cenario"]["luz"]}
    json.dump(spec, open(os.path.join(out_dir, "composicao.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return os.path.join(out_dir, "composicao.json")
