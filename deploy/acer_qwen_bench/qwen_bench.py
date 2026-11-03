"""Teste isolado do Qwen3-8B como diretor criativo no Acer (só biblioteca padrão do Python).

Roda os 15 briefings de mensagens.json no llama-server (llama.cpp, CUDA) e mede tempo, tokens, VRAM e RAM.
NÃO toca no agente, no ComfyUI, no workflow nem no Gemma de produção:
  - recusa rodar se o ComfyUI (127.0.0.1:8188) ou o agente (workers.acer_agent) estiverem ativos — os dois modelos
    nunca ficam carregados ao mesmo tempo na placa de 6 GB;
  - usa só a pasta onde está (modelo, binários e resultado ficam aqui; apagar a pasta desfaz tudo);
  - o servidor do modelo escuta só em 127.0.0.1 e é encerrado no fim.

Uso (PowerShell, na pasta do teste):
  python qwen_bench.py --baixar      # baixa llama.cpp b12074 (CUDA 12.4) e Qwen3-8B-Q4_K_M.gguf (~5 GB)
  python qwen_bench.py               # roda os 15 casos e grava resultados_qwen.json
  python qwen_bench.py --modelo Qwen3-4B-Q4_K_M.gguf   # (reserva) mesmo teste com o 4B
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LLAMA_TAG = "b12074"
FILES = {
    f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip":
        f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip",
    "cudart-llama-bin-win-cuda-12.4-x64.zip":
        f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/cudart-llama-bin-win-cuda-12.4-x64.zip",
    "Qwen3-8B-Q4_K_M.gguf": "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
}
OPTIONAL = {"Qwen3-4B-Q4_K_M.gguf": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"}
PORT = 8791
BIN = os.path.join(HERE, "llama")


def download(name, url):
    dest = os.path.join(HERE, name)
    if os.path.exists(dest):
        print("já existe:", name)
        return dest
    print("baixando", name, "…", flush=True)
    tmp = dest + ".part"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sitegenius-bench"}), timeout=60) as r, \
            open(tmp, "wb") as f:
        total, got, last = int(r.headers.get("Content-Length") or 0), 0, 0
        while True:
            chunk = r.read(4 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if time.time() - last > 10:
                last = time.time()
                print(f"  {got / 1e9:.2f} / {total / 1e9:.2f} GB", flush=True)
    os.replace(tmp, dest)
    return dest


def setup(extra):
    for name, url in {**FILES, **({k: v for k, v in OPTIONAL.items() if k in extra})}.items():
        path = download(name, url)
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                z.extractall(BIN)
    print("pronto. llama-server em", BIN)


def port_open(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


def agent_running():
    cmd = ["powershell", "-NoProfile", "-Command",
           "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Select-Object -ExpandProperty CommandLine"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return False
    return "acer_agent" in out


def gpu():
    """(usada_MB, total_MB) da GPU via nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        used, total = (int(x) for x in out.split(","))
        return used, total
    except Exception:  # noqa: BLE001
        return None, None


def ram_free_mb():
    cmd = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"]
    try:
        return int(subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()) // 1024
    except Exception:  # noqa: BLE001
        return None


def server_ram_mb():
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq llama-server.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().split('","')[-1].replace('"', "").replace(" K", "").replace(".", "").replace(",", "")) // 1024
    except Exception:  # noqa: BLE001
        return None


class Sampler(threading.Thread):
    """Pico de VRAM e de RAM do llama-server a cada 1 s."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.vram, self.ram = 0, 0

    def run(self):
        while not self.stop.is_set():
            used, _ = gpu()
            self.vram = max(self.vram, used or 0)
            self.ram = max(self.ram, server_ram_mb() or 0)
            self.stop.wait(1.0)


def post(path, body, timeout):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def start_server(model, ngl, ctx):
    exe = next((os.path.join(d, "llama-server.exe") for d, _, fs in os.walk(BIN) if "llama-server.exe" in fs), None)
    if not exe:
        raise SystemExit("llama-server.exe não encontrado: rode primeiro com --baixar")
    log = open(os.path.join(HERE, f"servidor_ngl{ngl}.log"), "w", encoding="utf-8", errors="ignore")
    proc = subprocess.Popen([exe, "-m", os.path.join(HERE, model), "--host", "127.0.0.1", "--port", str(PORT),
                             "-ngl", str(ngl), "-c", str(ctx), "-np", "1", "--jinja", "--no-webui"],
                            stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 300:
        if proc.poll() is not None:
            return None, round(time.time() - t0, 1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return proc, round(time.time() - t0, 1)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    proc.kill()
    return None, round(time.time() - t0, 1)


def comfy_idle():
    """ComfyUI ligado e SEM nada na fila (nem rodando, nem esperando)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=5) as r:
            q = json.loads(r.read())
        return not q.get("queue_running") and not q.get("queue_pending")
    except Exception:  # noqa: BLE001
        return False


def stop(proc):
    proc.terminate()
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        proc.kill()


def load(model, ctx, ngls):
    for ngl in ngls:  # tudo na GPU; se não couber, divide entre GPU e RAM
        proc, load_s = start_server(model, ngl, ctx)
        if proc:
            return proc, load_s, ngl
        print(f"não carregou com -ngl {ngl}; tentando menos camadas na GPU", flush=True)
    return None, None, None


def plan(msgs):
    body = {"messages": msgs, "temperature": 0.6, "max_tokens": 900, "top_p": 0.9, "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    try:
        r = post("/v1/chat/completions", body, timeout=600)
        tm = r.get("timings") or {}
        return {"s": round(time.time() - t0, 1), "texto": r["choices"][0]["message"].get("content") or "",
                "tokens_prompt": (r.get("usage") or {}).get("prompt_tokens"), "tokens_saida": (r.get("usage") or {}).get("completion_tokens"),
                "tok_s": round(tm.get("predicted_per_second") or 0, 1), "prompt_ms": round(tm.get("prompt_ms") or 0)}
    except Exception as exc:  # noqa: BLE001
        return {"s": round(time.time() - t0, 1), "erro": f"{type(exc).__name__}: {exc}"[:300], "texto": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baixar", action="store_true")
    ap.add_argument("--com-4b", action="store_true", help="com --baixar: baixa também o Qwen3-4B")
    ap.add_argument("--modelo", default="Qwen3-8B-Q4_K_M.gguf")
    ap.add_argument("--ctx", type=int, default=6144)
    ap.add_argument("--casos", nargs="*")
    ap.add_argument("--saida", default="resultados_qwen.json")
    ap.add_argument("--com-comfyui-ocioso", action="store_true",
                    help="mede o modelo AO LADO do ComfyUI carregado e parado (fila vazia, agente parado). Nada é gerado no "
                         "ComfyUI e ele não é descarregado; serve para saber se o modelo cabe sem tirar o gerador da GPU")
    ap.add_argument("--sob-demanda", action="store_true",
                    help="carrega, planeja e descarrega o modelo a CADA briefing (tempo total real de um uso por arte)")
    args = ap.parse_args()
    if args.baixar:
        setup({"Qwen3-4B-Q4_K_M.gguf"} if args.com_4b else set())
        return
    if agent_running():
        raise SystemExit("Agente do SiteGenius (workers.acer_agent) ativo: pare-o antes do teste e reinicie depois.")
    if args.com_comfyui_ocioso:
        if not port_open(8188) or not comfy_idle():
            raise SystemExit("Este modo exige o ComfyUI ligado, com o modelo carregado e a fila vazia.")
        ngls = (99,)  # ao lado do ComfyUI: só vale se couber INTEIRO na GPU (sem apertar a RAM do gerador)
    else:
        if port_open(8188):
            raise SystemExit("ComfyUI ativo em 127.0.0.1:8188: feche-o (ou use --com-comfyui-ocioso só com o 4B).")
        ngls = (99, 30, 24, 16)
    msgs = json.load(open(os.path.join(HERE, "mensagens.json"), encoding="utf-8"))
    names = args.casos or list(msgs)
    base_vram, total_vram = gpu()
    base_ram = ram_free_mb()
    print(f"VRAM usada antes: {base_vram}/{total_vram} MB; RAM livre: {base_ram} MB", flush=True)
    out_path = os.path.join(HERE, args.saida)
    out = {"modelo": args.modelo, "llama_cpp": LLAMA_TAG, "ctx": args.ctx, "modo": "com_comfyui_ocioso" if args.com_comfyui_ocioso
           else "sob_demanda" if args.sob_demanda else "carregado", "vram_antes_mb": base_vram, "vram_total_mb": total_vram,
           "ram_livre_antes_mb": base_ram, "casos": {}}
    sampler = Sampler()
    sampler.start()
    proc = None
    try:
        if not args.sob_demanda:
            proc, load_s, ngl = load(args.modelo, args.ctx, ngls)
            if not proc:
                raise SystemExit("o modelo não carregou (ver servidor_ngl*.log)" + (" — não cabe ao lado do ComfyUI" if args.com_comfyui_ocioso else ""))
            out.update({"ngl": ngl, "carga_s": load_s, "vram_carregado_mb": gpu()[0]})
            print(f"carregado com -ngl {ngl} em {load_s}s; VRAM usada: {out['vram_carregado_mb']} MB", flush=True)
        for name in names:
            t0 = time.time()
            if args.sob_demanda:
                proc, load_s, ngl = load(args.modelo, args.ctx, ngls)
                if not proc:
                    out["casos"][name] = {"erro": "não carregou", "texto": ""}
                    continue
            case = plan(msgs[name])
            if args.sob_demanda:
                stop(proc)
                proc = None
                case.update({"carga_s": load_s, "ngl": ngl, "total_s": round(time.time() - t0, 1)})
            if args.com_comfyui_ocioso:
                case["comfyui_ok"] = comfy_idle()  # o gerador continua respondendo e parado
            out["casos"][name] = case
            print(name, case.get("s"), "s", case.get("tok_s"), "tok/s", case.get("total_s", ""), case.get("erro", ""), flush=True)
            json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    finally:
        sampler.stop.set()
        sampler.join(3)
        if proc:
            stop(proc)
    out.update({"vram_pico_mb": sampler.vram, "ram_servidor_pico_mb": sampler.ram, "ram_livre_depois_mb": ram_free_mb()})
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("resultado em", out_path)


if __name__ == "__main__":
    sys.exit(main())
