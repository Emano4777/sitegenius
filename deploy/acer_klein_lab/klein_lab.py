"""Executor isolado do FLUX.2 Klein no Acer (só biblioteca padrão do Python).

Recebe as solicitações da PE0 numa FILA, executa o Klein no ComfyUI local (127.0.0.1:8188) e devolve as imagens e as
métricas (tempo, VRAM, resolução). Também faz a super-resolução na GPU do Acer (Real-ESRGAN pelo ComfyUI) quando o
tamanho final pedido é maior que o que o Klein gera de uma vez.

Não altera o agente do SiteGenius, o workflow de produção nem os modelos. Para não disputar com a produção:
  - só executa quando o ComfyUI está com a fila vazia E ocioso há SG_LAB_OCIOSO_S segundos (padrão 120);
  - opcionalmente só dentro de uma janela de horário (--janela 00-07);
  - ao terminar um lote, libera os modelos do Klein da memória (a próxima geração de produção carrega o Z-Image).

Filas:
  --fila pasta:C:\\caminho   entrada\\*.json -> saida\\<id>\\(imagem.png, ampliada.png, metricas.json)  (pasta sincronizada)
  --fila supabase           tabela sitegenius_lab.sg_lab_jobs (variável SG_LAB_DATABASE_URL; papel restrito à tabela)

Uso: python klein_lab.py --workflow C:\\IA-Imagens\\flux2-klein-lab\\klein_api.json --fila pasta:C:\\IA-Imagens\\klein-fila
     python klein_lab.py ... --uma-vez        (processa o que houver e sai)
"""
import argparse
import base64
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

COMFY = "http://127.0.0.1:8188"
HERE = os.path.dirname(os.path.abspath(__file__))
UPSCALE_MODEL = os.getenv("SG_LAB_UPSCALE_MODEL", "RealESRGAN_x4plus.pth")


def http(method, path, body=None, timeout=60, raw=False):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(COMFY + path, data=data, method=method,
                                 headers={} if isinstance(body, bytes) else {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = r.read()
    return out if raw else json.loads(out or b"{}")


def gpu_used():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        return [int(x) for x in out.split(",")]
    except Exception:  # noqa: BLE001
        return [None, None]


class Peak(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.vram = threading.Event(), 0

    def run(self):
        while not self.stop.is_set():
            self.vram = max(self.vram, gpu_used()[0] or 0)
            self.stop.wait(0.7)


# ---------------------------------------------------------------- ComfyUI
def comfy_idle(min_idle_s):
    """Fila vazia e nenhuma execução terminada nos últimos min_idle_s segundos (produção tem prioridade)."""
    try:
        q = http("GET", "/queue", timeout=5)
        if q.get("queue_running") or q.get("queue_pending"):
            return False
        hist = http("GET", "/history?max_items=1", timeout=5)
        for item in hist.values():
            msgs = (item.get("status") or {}).get("messages") or []
            ts = max([m[1].get("timestamp", 0) for m in msgs if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict)] or [0])
            if ts and time.time() - ts / 1000 < min_idle_s:
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


def patch_workflow(wf, prompt, w, h, seed, prefix):
    """Ajusta qualquer workflow de API do ComfyUI pelo TIPO dos nós: texto, tamanho, semente e nome do arquivo."""
    wf = json.loads(json.dumps(wf))
    texts = [(k, n) for k, n in wf.items() if isinstance(n.get("inputs", {}).get("text"), str)]
    if not texts:
        raise ValueError("workflow sem nó de texto")
    # o prompt positivo é o nó de texto mais longo do modelo (o negativo, se houver, costuma ser vazio/curto)
    k, node = max(texts, key=lambda kn: len(kn[1]["inputs"]["text"]))
    node["inputs"]["text"] = prompt
    sized = 0
    for n in wf.values():
        inp = n.get("inputs", {})
        if isinstance(inp.get("width"), int) and isinstance(inp.get("height"), int):
            inp["width"], inp["height"] = w, h
            sized += 1
        for s in ("seed", "noise_seed"):
            if isinstance(inp.get(s), int):
                inp[s] = seed
        if n.get("class_type") == "SaveImage":
            inp["filename_prefix"] = prefix
    if not sized:
        raise ValueError("workflow sem nó de tamanho (width/height)")
    return wf


def run_prompt(wf, timeout=900):
    pid = http("POST", "/prompt", {"prompt": wf, "client_id": uuid.uuid4().hex})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = http("GET", f"/history/{pid}", timeout=10)
        if pid in h:
            item = h[pid]
            if (item.get("status") or {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI: erro na execução")
            for out in (item.get("outputs") or {}).values():
                for im in out.get("images") or []:
                    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")})
                    return http("GET", f"/view?{q}", raw=True, timeout=60), round(time.time() - t0, 1)
        time.sleep(1.0)
    raise TimeoutError("ComfyUI não terminou a tempo")


def upscale_available():
    try:
        info = http("GET", "/object_info/UpscaleModelLoader", timeout=10)
        models = info["UpscaleModelLoader"]["input"]["required"]["model_name"][0]
        return UPSCALE_MODEL if UPSCALE_MODEL in models else None
    except Exception:  # noqa: BLE001
        return None


def upscale(png_bytes, name):
    """Super-resolução na GPU (Real-ESRGAN x4 pelo ComfyUI). A PE0 reduz depois para o tamanho exato (Lanczos)."""
    boundary = uuid.uuid4().hex
    fname = f"sglab_{uuid.uuid4().hex[:8]}.png"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{fname}\"\r\nContent-Type: image/png\r\n\r\n"
            ).encode() + png_bytes + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(COMFY + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        up = json.loads(r.read())
    wf = {"1": {"class_type": "LoadImage", "inputs": {"image": up["name"]}},
          "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": name}},
          "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
          "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "sglab_up"}}}
    return run_prompt(wf, timeout=600)


def free_models():
    try:
        http("POST", "/free", {"unload_models": True, "free_memory": True}, timeout=20)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- filas
class FolderQueue:
    def __init__(self, path):
        self.inp, self.out = os.path.join(path, "entrada"), os.path.join(path, "saida")
        os.makedirs(self.inp, exist_ok=True)
        os.makedirs(self.out, exist_ok=True)

    def pending(self):
        for f in sorted(os.listdir(self.inp)):
            if f.endswith(".json") and not os.path.exists(os.path.join(self.out, f[:-5], "metricas.json")):
                yield json.load(open(os.path.join(self.inp, f), encoding="utf-8"))

    def done(self, job, files, metrics):
        d = os.path.join(self.out, job["id"])
        os.makedirs(d, exist_ok=True)
        for name, data in files.items():
            open(os.path.join(d, name), "wb").write(data)
        json.dump(metrics, open(os.path.join(d, "metricas.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


class SupabaseQueue:
    """Tabela isolada (sitegenius_lab.sg_lab_jobs): id, payload jsonb, status, result jsonb, imagem bytea, ampliada bytea."""

    def __init__(self):
        import psycopg2  # noqa: PLC0415 — só neste modo
        self.conn = psycopg2.connect(os.environ["SG_LAB_DATABASE_URL"])
        self.conn.autocommit = True

    def pending(self):
        cur = self.conn.cursor()
        cur.execute("UPDATE sitegenius_lab.sg_lab_jobs SET status='rodando', atualizado=now() WHERE id = (SELECT id FROM "
                    "sitegenius_lab.sg_lab_jobs WHERE status='fila' ORDER BY criado LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, payload")
        row = cur.fetchone()
        if row:
            yield {**row[1], "id": row[0]}

    def done(self, job, files, metrics):
        import psycopg2  # noqa: PLC0415
        cur = self.conn.cursor()
        cur.execute("UPDATE sitegenius_lab.sg_lab_jobs SET status=%s, result=%s, imagem=%s, ampliada=%s, atualizado=now() WHERE id=%s",
                    ("erro" if metrics.get("erro") else "pronto", json.dumps(metrics), psycopg2.Binary(files.get("imagem.png", b"")),
                     psycopg2.Binary(files.get("ampliada.png", b"")), job["id"]))


# ---------------------------------------------------------------- execução
def process(job, wf, up_model):
    seed = int(job.get("seed") or random.randrange(1, 2**31))
    w, h = int(job["gen_w"]), int(job["gen_h"])
    peak = Peak()
    peak.start()
    metrics = {"id": job["id"], "abordagem": job.get("abordagem"), "geracao": [w, h], "seed": seed,
               "vram_antes_mb": gpu_used()[0]}
    files = {}
    try:
        data, secs = run_prompt(patch_workflow(wf, job["prompt"], w, h, seed, f"sglab_{job['id']}"))
        files["imagem.png"] = data
        metrics["geracao_s"] = secs
        if float(job.get("fator") or 1) > 1.05:
            if up_model:
                up, us = upscale(data, up_model)
                files["ampliada.png"] = up
                metrics.update({"ampliacao_s": us, "ampliacao": up_model})
            else:
                metrics["ampliacao"] = "indisponível (modelo de super-resolução não instalado no ComfyUI)"
    except Exception as exc:  # noqa: BLE001
        metrics["erro"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        peak.stop.set()
        peak.join(2)
    metrics["vram_pico_mb"] = peak.vram
    return files, metrics


def in_window(window):
    if not window:
        return True
    a, b = (int(x) for x in window.split("-"))
    hnow = time.localtime().tm_hour
    return a <= hnow < b if a < b else hnow >= a or hnow < b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True, help="workflow do Klein exportado em formato de API do ComfyUI")
    ap.add_argument("--fila", default=os.path.join(HERE, "fila"))
    ap.add_argument("--janela", default="", help="horas permitidas, ex.: 00-07 (vazio = qualquer hora)")
    ap.add_argument("--uma-vez", action="store_true")
    args = ap.parse_args()
    wf = json.load(open(args.workflow, encoding="utf-8"))
    patch_workflow(wf, "teste", 64, 64, 1, "x")  # valida o workflow logo no início
    q = SupabaseQueue() if args.fila == "supabase" else FolderQueue(args.fila.split(":", 1)[1] if args.fila.startswith("pasta:") else args.fila)
    idle_s = int(os.getenv("SG_LAB_OCIOSO_S", "120"))
    print("executor do Klein pronto; fila:", args.fila, flush=True)
    while True:
        ran = 0
        if in_window(args.janela):
            up_model = upscale_available()
            for job in q.pending():
                while not comfy_idle(idle_s):  # produção primeiro: espera o ComfyUI ficar ocioso
                    time.sleep(15)
                files, metrics = process(job, wf, up_model)
                q.done(job, files, metrics)
                ran += 1
                print(job["id"], metrics.get("geracao_s"), "s", metrics.get("vram_pico_mb"), "MB", metrics.get("erro", ""), flush=True)
            if ran:
                free_models()  # tira o Klein da GPU ao fim do lote
        if args.uma_vez:
            return 0
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main())
