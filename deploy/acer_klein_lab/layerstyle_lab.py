"""Compositor LayerStyle (ComfyUI_LayerStyle) numa instância ISOLADA do ComfyUI (porta 8189, só CPU).

Recebe da PE0 a MESMA composição usada pelos outros compositores (fila_ls\\entrada\\<id>\\composicao.json + cenário,
fotos e fontes), copia as fontes para o LayerStyle, liga a instância de laboratório, monta o fluxo com os nós do
LayerStyle (texto, sombra, contorno, brilho, mesclagem) e devolve fila_ls\\saida\\<id>\\(arte.png, metricas.json).
Antes de montar, lê o catálogo de nós (/object_info): entradas desconhecidas recebem o valor padrão do próprio nó,
e o catálogo vai junto no resultado (object_info_layerstyle.json), para ajuste fino na PE0.

Uso: python layerstyle_lab.py --comfyui C:\\IA-Imagens\\comfyui-lab --fila C:\\IA-Imagens\\klein-lab\\fila_ls
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFont

PORT = 8189
URL = f"http://127.0.0.1:{PORT}"


def http(method, path, body=None, timeout=120, raw=False, ctype="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(URL + path, data=data, method=method, headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = r.read()
    return out if raw else json.loads(out or b"{}")


def start_lab(comfy_dir):
    py = os.path.join(comfy_dir, "venv", "Scripts", "python.exe")
    log = open(os.path.join(comfy_dir, "lab_8189.log"), "w", encoding="utf-8", errors="ignore")
    proc = subprocess.Popen([py, os.path.join(comfy_dir, "main.py"), "--listen", "127.0.0.1", "--port", str(PORT), "--cpu",
                             "--disable-auto-launch"], cwd=comfy_dir, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 300:
        try:
            http("GET", "/system_stats", timeout=3)
            return proc, round(time.time() - t0, 1)
        except Exception:  # noqa: BLE001
            if proc.poll() is not None:
                raise RuntimeError("a instância de laboratório não iniciou (ver lab_8189.log)")
            time.sleep(2)
    proc.kill()
    raise TimeoutError("a instância de laboratório não respondeu")


def upload(path):
    boundary = uuid.uuid4().hex
    name = f"ls_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n"
            ).encode() + open(path, "rb").read() + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    return http("POST", "/upload/image", body, ctype=f"multipart/form-data; boundary={boundary}")["name"]


class Builder:
    """Monta o fluxo a partir do catálogo REAL de nós: padrão do nó + só as entradas que existem."""

    def __init__(self, info):
        self.info, self.wf, self.n = info, {}, 0

    def find(self, *keys):
        for k in keys:
            hit = next((c for c in self.info if k.lower() in c.lower()), None)
            if hit:
                return hit
        return None

    def node(self, cls, **inputs):
        spec = self.info[cls]["input"]
        vals = {}
        for group in ("required", "optional"):
            for name, sp in (spec.get(group) or {}).items():
                if name in inputs:
                    vals[name] = inputs[name]
                elif group == "required":
                    if isinstance(sp[0], list):
                        vals[name] = sp[0][0]
                    elif len(sp) > 1 and isinstance(sp[1], dict) and "default" in sp[1]:
                        vals[name] = sp[1]["default"]
        self.n += 1
        self.wf[str(self.n)] = {"class_type": cls, "inputs": vals}
        return str(self.n)


def fit_size(text, font, w, h):
    lines = text.split("\n")
    s = 400
    while s > 8:
        f = ImageFont.truetype(font, s)
        bw = max(f.getbbox(ln)[2] - f.getbbox(ln)[0] for ln in lines)
        bh = sum(f.getbbox(ln)[3] - f.getbbox(ln)[1] for ln in lines) * 1.15
        if bw <= w * 0.96 and bh <= h * 0.9:
            return s
        s -= 4
    return 8


def shape_png(path, w, h, color, kind):
    im = Image.new("RGBA", (max(2, int(w)), max(2, int(h))), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    if kind == "explosao":
        cx, cy, r = w / 2, h / 2, min(w, h) / 2
        d.polygon([(cx + (r if k % 2 == 0 else r * 0.8) * math.cos(k * math.pi / 16), cy + (r if k % 2 == 0 else r * 0.8) * math.sin(k * math.pi / 16))
                   for k in range(32)], fill=rgb)
    elif kind in ("circulo", "carimbo"):
        d.ellipse((0, 0, w - 1, h - 1), fill=rgb)
    else:
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=int(min(w, h) * 0.18), fill=rgb)
    im.save(path)


def compose(job_dir, out_dir, info, font_dir):
    spec = json.load(open(os.path.join(job_dir, "composicao.json"), encoding="utf-8"))
    W, H = spec["tamanho"]
    B = Builder(info)
    load = B.find("LoadImage")
    blend = B.find("LayerUtility: ImageBlendAdvance", "ImageBlendAdvance")
    text_cls = B.find("LayerUtility: SimpleTextImage", "SimpleTextImage", "LayerUtility: TextImage")
    shadow_cls, stroke_cls, glow_cls = B.find("LayerStyle: DropShadow"), B.find("LayerStyle: Stroke"), B.find("LayerStyle: OuterGlow")
    missing = [n for n, c in (("mesclagem", blend), ("texto", text_cls)) if not c]
    if missing:
        raise RuntimeError("nós do LayerStyle ausentes: " + ", ".join(missing))
    sc = Image.open(os.path.join(job_dir, os.path.basename(spec["cenario"]))).convert("RGB").resize((W, H), Image.LANCZOS)
    scp = os.path.join(out_dir, "_cenario.png")
    sc.save(scp)
    cur = (B.node(load, image=upload(scp)), 0)
    used = []
    for el in spec["elementos"]:
        x, y, w, h = (el["b"][0] * W / 100, el["b"][1] * H / 100, el["b"][2] * W / 100, el["b"][3] * H / 100)
        layers = []
        if el["tipo"] == "produto":
            im = Image.open(os.path.join(job_dir, os.path.basename(el["foto"]))).convert("RGBA")
            f = min(w / im.width, h / im.height)
            p = os.path.join(out_dir, f"_p{len(used)}.png")
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))))
            canvas.paste(im, (int(x + (w - im.width) / 2), int(y + h - im.height)), im)
            canvas.save(p)
            layers.append(("img", p, None))
        if el["tipo"] in ("preco", "selo"):
            p = os.path.join(out_dir, f"_s{len(used)}.png")
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shape_png(p + ".shape.png", w, h, el.get("bg") or el.get("cor") or "#ffd400", el.get("estilo") or el.get("forma"))
            sh = Image.open(p + ".shape.png")
            canvas.paste(sh, (int(x), int(y)), sh)
            canvas.save(p)
            layers.append(("img", p, None))
        txt = el.get("texto") or ("R$ " + el["inteiro"] + (el.get("centavos") or "") + (" " + el["sufixo"] if el.get("sufixo") else "")
                                  if el["tipo"] == "preco" else None)
        if el["tipo"] == "lista":
            txt = "\n".join(f"{a}   {b}" for a, b in el["linhas"])
        if txt and el.get("fonte"):
            fname = os.path.basename(el["fonte"])
            if not os.path.exists(os.path.join(font_dir, fname)):
                shutil.copy(os.path.join(job_dir, fname), os.path.join(font_dir, fname))
            layers.append(("txt", txt, el))
        for kind, a, e2 in layers:
            if kind == "img":
                ld = B.node(load, image=upload(a))
                cur = (B.node(blend, background_image=list(cur), layer_image=[ld, 0], layer_mask=[ld, 1], x_percent=50.0, y_percent=50.0,
                              scale=1.0, opacity=100, blend_mode="normal"), 0)
                used.append(a)
                continue
            fx = (e2.get("efeito") or {})
            color = ((fx.get("preenchimento") or {}).get("cores") or [e2.get("cor") or e2.get("fg") or "#ffffff"])[0]
            size = fit_size(a, os.path.join(font_dir, os.path.basename(e2["fonte"])), w, h)
            t = B.node(text_cls, text=a, font_file=os.path.basename(e2["fonte"]), font_size=size, size=size, text_color=color,
                       align="center", width=W, height=H, x_offset=int(x + w / 2 - W / 2), y_offset=int(y + h / 2 - H / 2),
                       leading=int(size * 0.15), char_per_line=200, stroke_width=0)
            if shadow_cls:
                for s in (fx.get("sombras") or [{"x": 0, "y": 0.05, "desfoque": 0.1, "cor": "#000000", "opacidade": 0.5}])[:1]:
                    cur = (B.node(shadow_cls, background_image=list(cur), layer_image=[t, 0], layer_mask=[t, 1], distance_x=int(s["x"] * size),
                                  distance_y=int(s["y"] * size), blur=int(s["desfoque"] * size), shadow_color=s["cor"], opacity=int(s["opacidade"] * 100)), 0)
            if stroke_cls and fx.get("contornos"):
                o = fx["contornos"][0]
                cur = (B.node(stroke_cls, background_image=list(cur), layer_image=[t, 0], layer_mask=[t, 1], stroke_width=max(1, int(o["espessura"] * size * 2)),
                              stroke_color=o["cor"], stroke_grow=0, blur=0, opacity=100), 0)
            if glow_cls and fx.get("brilho"):
                cur = (B.node(glow_cls, background_image=list(cur), layer_image=[t, 0], layer_mask=[t, 1], glow_color=fx["brilho"]["cor"],
                              blur=int(size * 0.2), light_width=int(size * 0.06), brightness=int(fx["brilho"]["intensidade"] * 10)), 0)
            cur = (B.node(blend, background_image=list(cur), layer_image=[t, 0], layer_mask=[t, 1], x_percent=50.0, y_percent=50.0,
                          scale=1.0, opacity=100, blend_mode="normal"), 0)
    B.node("SaveImage", images=list(cur), filename_prefix="ls_arte")
    t0 = time.time()
    pid = http("POST", "/prompt", {"prompt": B.wf, "client_id": uuid.uuid4().hex})["prompt_id"]
    while time.time() - t0 < 900:
        h_ = http("GET", f"/history/{pid}", timeout=20)
        if pid in h_:
            item = h_[pid]
            if (item.get("status") or {}).get("status_str") == "error":
                json.dump(item.get("status"), open(os.path.join(out_dir, "erro_comfy.json"), "w"), indent=1)
                raise RuntimeError("erro no fluxo do LayerStyle (ver erro_comfy.json)")
            for out in (item.get("outputs") or {}).values():
                for im in out.get("images") or []:
                    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")})
                    open(os.path.join(out_dir, "arte.png"), "wb").write(http("GET", f"/view?{q}", raw=True))
                    return round(time.time() - t0, 1), len(B.wf)
        time.sleep(1)
    raise TimeoutError("o fluxo não terminou")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfyui", default=r"C:\IA-Imagens\comfyui-lab")
    ap.add_argument("--fila", default=r"C:\IA-Imagens\klein-lab\fila_ls")
    args = ap.parse_args()
    inp, outp = os.path.join(args.fila, "entrada"), os.path.join(args.fila, "saida")
    os.makedirs(outp, exist_ok=True)
    jobs = [d for d in sorted(os.listdir(inp)) if os.path.isdir(os.path.join(inp, d)) and not os.path.exists(os.path.join(outp, d, "metricas.json"))]
    if not jobs:
        print("nada na fila")
        return 0
    font_dir = os.path.join(args.comfyui, "custom_nodes", "ComfyUI_LayerStyle", "font")
    os.makedirs(font_dir, exist_ok=True)
    for d in jobs:  # as fontes precisam estar na pasta do LayerStyle ANTES de a instância iniciar
        for f in os.listdir(os.path.join(inp, d)):
            if f.lower().endswith((".ttf", ".otf")) and not os.path.exists(os.path.join(font_dir, f)):
                shutil.copy(os.path.join(inp, d, f), os.path.join(font_dir, f))
    proc, boot = start_lab(args.comfyui)
    try:
        info = http("GET", "/object_info")
        ls = {k: v for k, v in info.items() if "Layer" in k or k in ("LoadImage", "SaveImage")}
        json.dump(ls, open(os.path.join(outp, "object_info_layerstyle.json"), "w", encoding="utf-8"), indent=1)
        for d in jobs:
            od = os.path.join(outp, d)
            os.makedirs(od, exist_ok=True)
            m = {"id": d, "inicio_instancia_s": boot}
            try:
                m["compor_s"], m["nos"] = compose(os.path.join(inp, d), od, info, font_dir)
            except Exception as exc:  # noqa: BLE001
                m["erro"] = f"{type(exc).__name__}: {exc}"[:400]
            json.dump(m, open(os.path.join(od, "metricas.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(d, m, flush=True)
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
