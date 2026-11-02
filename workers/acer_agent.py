"""Agente de renderização do Acer Nitro (Emanuelomelho).

Busca tarefas no SiteGenius por HTTPS assinado (só saída) e gera no ComfyUI local
(127.0.0.1:8188, nunca exposto).

Tipos de tarefa:
  * free_image (v2): imagem livre entregue direto, sem cards, preços ou logo.
    txt2img, ou img2img quando há referência/imagem anterior: a imagem vai para o
    ComfyUI (/upload/image) e entra no workflow por LoadImage -> VAEEncode com denoise < 1.
  * ad_composite (v1/v2): cenário no ComfyUI + encarte montado por studio/compose.py.
  * v2.3: com textos pedidos, aplica a tipografia aqui (studio/typography.py: superfícies, validação) e confere
    com leitura visual local (studio/ocr.py, RapidOCR na CPU, opcional). SG_AGENT_COMPOSE=0 deixa para o servidor;
    SG_OCR=0 desliga a leitura visual.

Desempenho:
  * O modelo fica carregado entre tarefas. O agente NÃO chama mais /free com free_memory a cada
    tarefa: no ComfyUI isso limpa o cache dos nós e obriga a recarregar UNet, codificador e VAE.
    Só libera se a RAM livre cair abaixo de SG_COMFY_MIN_FREE_RAM_MB (padrão 1200).
  * Progresso real pelo WebSocket do ComfyUI (pacote opcional websocket-client): etapa atual
    (carregar modelo, codificar texto, amostragem passo a passo, VAE) e tempo de cada etapa.
    Sem o pacote, cai para o acompanhamento por /history.
  * Passos (steps) e tamanho vêm do perfil escolhido no servidor (Rápido/Qualidade).

Variáveis de ambiente (arquivo workers/.env, fora do Git):
    SG_BASE_URL, SG_WORKER_ID, SG_WORKER_SECRET, SG_VERCEL_BYPASS
    COMFY_URL                 http://127.0.0.1:8188
    SG_COMFY_WORKFLOW         workflow txt2img em "API format" com {{PROMPT}} {{SEED}} {{WIDTH}} {{HEIGHT}}
    SG_POLL_SECONDS           intervalo de busca (padrão 10)
    SG_SCENE_TIMEOUT          limite de uma geração em segundos (padrão 900)
    SG_COMFY_MIN_FREE_RAM_MB  abaixo disso o agente libera memória (força recarga na próxima) — padrão 1200
    SG_COMFY_FREE_AFTER_JOB   1 = comportamento antigo (liberar sempre; não recomendado)
    SG_COMFY_CLIP_DEVICE      default | cpu — onde roda o codificador de texto (só se o nó aceitar "device").
                              cpu evita trocar o codificador e o modelo de imagem na VRAM a cada prompt novo.

Uso:
    python -m workers.acer_agent            # loop contínuo
    python -m workers.acer_agent --once     # processa no máximo uma tarefa
    python -m workers.acer_agent --check    # valida configuração, ComfyUI, img2img e WebSocket
    python -m workers.acer_agent --bench 640x640,768x960 --steps 6,9 --runs 3 [--report]
    python -m workers.acer_agent --bench-prompts --size 640x640 [--repeat] [--clip-device default,cpu] [--report]
    python -m workers.acer_agent --no-comfy # sem GPU (imagem de teste)
"""
import argparse
import copy
import io
import json
import logging
import os
import random
import statistics
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio.compose import compose_flyer  # noqa: E402
from studio.worker_client import WorkerClient  # noqa: E402

log = logging.getLogger("acer_agent")
PLACEHOLDERS = ("{{PROMPT}}", "{{SEED}}", "{{WIDTH}}", "{{HEIGHT}}")
LATENT_NODES = ("EmptySD3LatentImage", "EmptyLatentImage", "EmptyFlux2LatentImage")
HERE = os.path.dirname(os.path.abspath(__file__))
TIMINGS_FILE = os.path.join(HERE, ".timings.json")
STAGES = ("queue", "load", "prep", "encode", "sampling", "vae", "save")


class JobCanceled(Exception):
    pass


def load_env_file(path):
    """Leitor mínimo de .env (KEY=VALUE), sem sobrescrever o ambiente."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def loopback_only(url):
    host = urlsplit(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("COMFY_URL deve apontar para 127.0.0.1: o ComfyUI nunca deve ser exposto.")
    return url.rstrip("/")


def fill_workflow(template, prompt, seed, width, height):
    values = {"{{PROMPT}}": prompt, "{{SEED}}": seed, "{{WIDTH}}": width, "{{HEIGHT}}": height}

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            if node in values:
                return values[node]
            if "{{PROMPT}}" in node:
                return node.replace("{{PROMPT}}", prompt)
        return node

    return walk(template)


def set_steps(workflow, steps):
    """Aplica o número de passos do perfil (Rápido/Qualidade) em todos os KSampler."""
    if not steps:
        return workflow
    for node in workflow.values():
        if node.get("class_type") == "KSampler":
            node["inputs"]["steps"] = max(1, min(50, int(steps)))
    return workflow


def workflow_steps(workflow):
    return next((n["inputs"].get("steps") for n in workflow.values() if n.get("class_type") == "KSampler"), None)


def to_img2img(workflow, image_name, denoise):
    """Troca o latente vazio por LoadImage -> ImageScale -> VAEEncode e aplica o denoise no KSampler."""
    wf = copy.deepcopy(workflow)
    latent_id = next((k for k, v in wf.items() if v.get("class_type") in LATENT_NODES), None)
    if latent_id is None:
        raise RuntimeError("workflow sem nó de latente vazio: img2img indisponível")
    samplers = [k for k, v in wf.items() if v.get("class_type") == "KSampler" and v["inputs"].get("latent_image") == [latent_id, 0]]
    decoder = next((v for v in wf.values() if v.get("class_type") == "VAEDecode"), None)
    if not samplers or decoder is None:
        raise RuntimeError("workflow sem KSampler/VAEDecode: img2img indisponível")
    width, height = wf[latent_id]["inputs"]["width"], wf[latent_id]["inputs"]["height"]
    wf["sg_load"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    wf["sg_scale"] = {"class_type": "ImageScale", "inputs": {
        "image": ["sg_load", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "center"}}
    wf["sg_encode"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["sg_scale", 0], "vae": decoder["inputs"]["vae"]}}
    for sid in samplers:
        wf[sid]["inputs"]["latent_image"] = ["sg_encode", 0]
        wf[sid]["inputs"]["denoise"] = max(0.05, min(1.0, float(denoise)))
    del wf[latent_id]
    return wf


def history_seconds(entry):
    """(início da execução, duração) a partir das mensagens do /history (timestamps em ms)."""
    stamps = {}
    for item in (entry.get("status") or {}).get("messages") or []:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict) and "timestamp" in item[1]:
            stamps.setdefault(item[0], item[1]["timestamp"] / 1000)
    start, end = stamps.get("execution_start"), stamps.get("execution_success")
    return start, (end - start) if start and end else None


def stage_of(class_type):
    ct = class_type or ""
    if ct in ("LoadImage", "ImageScale"):
        return "prep"
    if "Loader" in ct or ct.startswith("ModelSampling"):
        return "load"
    if "TextEncode" in ct or ct.startswith("Conditioning"):
        return "encode"
    if "Sampler" in ct:
        return "sampling"
    if ct.startswith("VAEDecode") or ct.startswith("VAEEncode"):
        return "vae"
    if ct in ("SaveImage", "PreviewImage"):
        return "save"
    return "other"


class ProgressTracker:
    """Interpreta as mensagens do WebSocket do ComfyUI: etapa atual, passo e tempo de cada etapa."""

    def __init__(self, workflow, clock=time.time):
        self.node_stage = {str(k): stage_of(v.get("class_type")) for k, v in workflow.items()}
        self.clock = clock
        self.lock = threading.Lock()
        self.prompt_id = None
        self.created = clock()
        self.started = None
        self.finished = None
        self.error = None
        self.current = None  # (estágio, início)
        self.durations = {}
        self.step, self.max_steps = 0, 0
        self.cached = set()
        self.sampler_started = None
        self.first_step_at = None

    def _close(self, now):
        if self.current:
            stage, since = self.current
            self.durations[stage] = self.durations.get(stage, 0.0) + (now - since)
            self.current = None

    def feed(self, message, now=None):
        now = self.clock() if now is None else now
        kind, data = message.get("type"), message.get("data") or {}
        pid = data.get("prompt_id")
        with self.lock:
            if pid and self.prompt_id and pid != self.prompt_id:
                return
            if kind == "execution_start":
                self.started = now
                self.durations["queue"] = now - self.created
            elif kind == "execution_cached":
                self.cached.update(str(n) for n in data.get("nodes") or [])
            elif kind == "executing":
                self._close(now)
                node = data.get("node")
                if node is None:
                    self.finished = self.finished or now
                else:
                    self.current = (self.node_stage.get(str(node), "other"), now)
                    if self.current[0] == "sampling" and self.sampler_started is None:
                        self.sampler_started = now
            elif kind == "progress":
                self.step, self.max_steps = int(data.get("value") or 0), int(data.get("max") or 0)
                if self.first_step_at is None and self.sampler_started is not None:
                    self.first_step_at = now
            elif kind == "execution_success":
                self._close(now)
                self.finished = now
            elif kind in ("execution_error", "execution_interrupted"):
                self._close(now)
                self.error = data.get("exception_message") or kind

    def snapshot(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            durations = dict(self.durations)
            stage = None
            if self.current:
                stage = self.current[0]
                durations[stage] = durations.get(stage, 0.0) + (now - self.current[1])
            elif not self.started:
                stage = "queue"
            return {"stage": stage, "step": self.step, "max": self.max_steps, "durations": durations,
                    "loaders_cached": any(self.node_stage.get(n) == "load" for n in self.cached)}

    def timings(self):
        snap = self.snapshot()
        d = snap["durations"]
        out = {f"{k}_s": round(v, 2) for k, v in d.items() if k in ("load", "encode", "sampling", "vae", "prep")}
        if snap["max"]:
            out["steps"] = snap["max"]
            if d.get("sampling"):
                out["s_por_passo"] = round(d["sampling"] / snap["max"], 2)
        out["modelo_em_cache"] = 1 if snap["loaders_cached"] or d.get("load", 0) < 1.0 else 0
        if self.sampler_started is not None and self.first_step_at is not None:
            # Do início do KSampler até o 1º passo: inclui (re)carregar o modelo de imagem na VRAM.
            out["preparo_amostragem_s"] = round(self.first_step_at - self.sampler_started, 2)
        return out


class LocalTimings:
    """Histórico local de tempos por tamanho/passos: a barra de progresso usa medição real, não chute."""

    def __init__(self, path=TIMINGS_FILE):
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    @staticmethod
    def key(width, height, steps=None, field="total"):
        return f"{width}x{height}" + (f"@{steps}" if steps else "") + ("" if field == "total" else f":{field}")

    def expected(self, width, height, steps=None, field="total"):
        values = self.data.get(self.key(width, height, steps, field)) or (
            self.data.get(self.key(width, height, None, field)) if steps else None)
        return statistics.median(values) if values else None

    def add(self, width, height, seconds, steps=None, field="total"):
        for k in {self.key(width, height, steps, field), self.key(width, height, None, field)}:
            values = self.data.setdefault(k, [])
            values.append(round(seconds, 1))
            del values[:-10]
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
        except OSError:
            pass


class Comfy:
    def __init__(self, base_url, workflow_path, timeout_s):
        self.base = loopback_only(base_url)
        self.timeout_s = timeout_s
        with open(workflow_path, encoding="utf-8") as fh:
            self.template = json.load(fh)
        text = json.dumps(self.template)
        missing = [p for p in PLACEHOLDERS if p not in text]
        if missing:
            raise SystemExit(f"Workflow sem os marcadores: {', '.join(missing)}")

    def check(self):
        resp = requests.get(self.base + "/system_stats", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def memory(self):
        try:
            stats = self.check()
        except requests.RequestException:
            return {}
        out = {"ram_livre_mb": (stats.get("system") or {}).get("ram_free", 0) / 2**20}
        devices = stats.get("devices") or []
        if devices:
            out["vram_livre_mb"] = devices[0].get("vram_free", 0) / 2**20
        return out

    def upload_image(self, data, name):
        resp = requests.post(
            self.base + "/upload/image",
            files={"image": (name, data, "image/png")},
            data={"overwrite": "true", "type": "input"},
            timeout=60,
        )
        resp.raise_for_status()
        info = resp.json()
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    def node_inputs(self, class_type):
        """Entradas aceitas por um nó (via /object_info), com cache."""
        cache = self.__dict__.setdefault("_inputs", {})
        if class_type not in cache:
            try:
                info = requests.get(f"{self.base}/object_info/{class_type}", timeout=10).json().get(class_type, {})
                spec = info.get("input", {})
                cache[class_type] = set(spec.get("required", {})) | set(spec.get("optional", {}))
            except (requests.RequestException, ValueError):
                cache[class_type] = set()
        return cache[class_type]

    def apply_clip_device(self, workflow, device):
        """Põe o codificador de texto no dispositivo pedido, se o nó tiver a entrada "device"."""
        if not device or device == "default":
            return workflow
        for node in workflow.values():
            ct = node.get("class_type", "")
            if ct.startswith("CLIPLoader") or ct.startswith("DualCLIPLoader"):
                if "device" in self.node_inputs(ct):
                    node["inputs"]["device"] = device
                else:
                    log.warning("nó %s não aceita 'device'; codificador continua no dispositivo padrão", ct)
        return workflow

    def build(self, prompt, seed, width, height, init_image=None, denoise=1.0, steps=None, clip_device=None):
        workflow = set_steps(fill_workflow(self.template, prompt, int(seed), int(width), int(height)), steps)
        workflow = self.apply_clip_device(workflow, clip_device or os.getenv("SG_COMFY_CLIP_DEVICE", "default"))
        if init_image:
            workflow = to_img2img(workflow, init_image, denoise)
        return workflow

    def _sample_memory(self, tracker, stop, out):
        """A cada 0,5 s: VRAM/RAM livres, guardando o mínimo por etapa (mostra trocas RAM <-> VRAM)."""
        while not stop.is_set():
            mem = self.memory()
            stage = (tracker.snapshot() or {}).get("stage") if getattr(tracker, "ws_ok", False) else None
            if mem.get("vram_livre_mb") is not None and stage in ("encode", "sampling", "vae"):
                key = f"vram_min_{stage}_mb"
                out[key] = min(out.get(key, 1e12), mem["vram_livre_mb"])
            if mem.get("ram_livre_mb") is not None:
                out["ram_min_mb"] = min(out.get("ram_min_mb", 1e12), mem["ram_livre_mb"])
            stop.wait(0.5)

    def _listen(self, client_id, tracker, stop):
        """Thread do WebSocket; se o pacote websocket-client não existir, só não há progresso fino."""
        try:
            import websocket  # pacote websocket-client
        except ImportError:
            return
        url = self.base.replace("http://", "ws://", 1) + f"/ws?clientId={client_id}"
        try:
            ws = websocket.create_connection(url, timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("WebSocket do ComfyUI indisponível (%s); progresso só por /history", type(exc).__name__)
            return
        ws.settimeout(1.0)
        tracker.ws_ok = True
        try:
            while not stop.is_set():
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:  # noqa: BLE001
                    break
                if isinstance(message, (bytes, bytearray)):
                    continue  # prévias binárias
                try:
                    tracker.feed(json.loads(message))
                except ValueError:
                    continue
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def run(self, workflow, on_tick):
        """Executa e devolve (bytes, tempos)."""
        client_id = str(uuid.uuid4())
        tracker = ProgressTracker(workflow)
        tracker.ws_ok = False
        stop = threading.Event()
        listener = threading.Thread(target=self._listen, args=(client_id, tracker, stop), daemon=True)
        listener.start()
        memory = {}
        threading.Thread(target=self._sample_memory, args=(tracker, stop, memory), daemon=True).start()
        time.sleep(0.3)  # deixa o WebSocket conectar antes de enfileirar (não perde os primeiros eventos)
        submitted = time.time()
        try:
            resp = requests.post(self.base + "/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
            if resp.status_code >= 400:
                raise RuntimeError(f"ComfyUI recusou o workflow: {resp.text[:300]}")
            prompt_id = resp.json()["prompt_id"]
            tracker.prompt_id = prompt_id
            last_history = 0.0
            while time.time() - submitted < self.timeout_s:
                time.sleep(1.0 if tracker.ws_ok else 2.0)
                try:
                    on_tick(time.time() - submitted, tracker.snapshot() if tracker.ws_ok else None)
                except JobCanceled:
                    requests.post(self.base + "/interrupt", timeout=10)
                    raise
                if tracker.error:
                    raise RuntimeError(f"ComfyUI: {tracker.error}"[:300])
                # Com WebSocket, o /history só é consultado quando a execução terminou (ou a cada 10 s, por garantia).
                if tracker.ws_ok and not tracker.finished and time.time() - last_history < 10:
                    continue
                last_history = time.time()
                entry = requests.get(f"{self.base}/history/{prompt_id}", timeout=15).json().get(prompt_id)
                if not entry:
                    continue
                if entry.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI retornou erro na geração (veja o terminal do ComfyUI)")
                for output in entry.get("outputs", {}).values():
                    for image in output.get("images", []):
                        finished = tracker.finished or time.time()
                        start, execution = history_seconds(entry)
                        fetch_start = time.time()
                        view = requests.get(
                            self.base + "/view",
                            params={"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                    "type": image.get("type", "output")},
                            timeout=30,
                        )
                        view.raise_for_status()
                        timings = {
                            "queue_comfy_s": max(0.0, start - submitted) if start else 0.0,
                            "comfy_s": execution if execution is not None else finished - submitted,
                            "fetch_s": time.time() - fetch_start,
                        }
                        if tracker.ws_ok:
                            timings.update(tracker.timings())
                        timings.update({k: round(v) for k, v in memory.items()})
                        return view.content, timings
            requests.post(self.base + "/interrupt", timeout=10)
            raise TimeoutError("a geração excedeu o tempo limite")
        finally:
            stop.set()

    def free_memory(self):
        """Libera cache do ComfyUI. ATENÇÃO: limpa o cache dos nós e força recarregar os modelos."""
        try:
            requests.post(self.base + "/free", json={"unload_models": False, "free_memory": True}, timeout=10)
        except requests.RequestException:
            pass

    def after_job(self):
        """Mantém o modelo carregado; só libera se a RAM estiver baixa (16 GB no Acer)."""
        if os.getenv("SG_COMFY_FREE_AFTER_JOB") == "1":
            self.free_memory()
            return "liberado (SG_COMFY_FREE_AFTER_JOB=1)"
        ram = self.memory().get("ram_livre_mb")
        limit = float(os.getenv("SG_COMFY_MIN_FREE_RAM_MB", "1200"))
        if ram is not None and ram < limit:
            self.free_memory()
            log.warning("RAM livre %.0f MB < %.0f MB: memória liberada (a próxima geração recarrega o modelo)", ram, limit)
            return "liberado (RAM baixa)"
        return "modelo mantido"


def _to_png(data):
    """Normaliza a imagem enviada ao ComfyUI (o servidor já recodificou; aqui só garante PNG)."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def placeholder_image(width, height, prompt):
    """--no-comfy: imagem de teste (não é IA) para validar o fluxo sem GPU."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (40, 44, 90))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(40 + 120 * y / height)
        draw.line([(0, y), (width, y)], fill=(shade, 60, 160 - shade // 3))
    draw.text((16, 16), "TESTE SEM GPU", fill=(255, 255, 255))
    draw.text((16, 36), prompt[:80], fill=(230, 230, 230))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def make_reporter(client, job_id):
    def report(progress, message, timings=None, info=None):
        try:
            client.progress(job_id, progress, message, timings, info)
        except RuntimeError as exc:
            raise JobCanceled() from exc
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                raise JobCanceled() from exc
            raise
    return report


STAGE_TEXT = {
    "queue": (6, "Na fila do ComfyUI"),
    "load": (9, "Carregando o modelo na GPU"),
    "prep": (12, "Preparando a imagem de referência"),
    "encode": (18, "Lendo o pedido (codificador de texto)"),
    "vae": (93, "Finalizando a imagem (VAE)"),
    "save": (96, "Salvando"),
}


def progress_message(snap, elapsed, expected_total, expected_vae):
    """(porcentagem, mensagem) a partir do estado real do ComfyUI; sem estado, usa a média medida."""
    if snap and snap.get("stage") == "sampling" and snap.get("max"):
        step, total = snap["step"], snap["max"]
        pct = 25 + int(65 * step / total)
        message = f"Gerando: passo {step}/{total}"
        sampling = snap["durations"].get("sampling", 0)
        if step:
            left = (total - step) * sampling / step + (expected_vae or 0)
            message += f" · ~{int(left)}s restantes"
        return pct, message
    if snap and snap.get("stage") in STAGE_TEXT:
        return STAGE_TEXT[snap["stage"]]
    if expected_total:
        pct = min(92, 15 + int(75 * elapsed / expected_total))
        return pct, f"Gerando ({int(elapsed)}s; faltam ~{max(0, int(expected_total - elapsed))}s pela média medida)"
    return min(90, 15 + int(elapsed / 6)), f"Gerando ({int(elapsed)}s)"


def progress_info(snap, expected_vae=None):
    """Etapa e passo reais para o servidor escrever a mensagem do cliente (sem termos técnicos)."""
    if not snap or not snap.get("stage"):
        return None
    info = {"etapa": snap["stage"] if snap["stage"] in ("queue", "load", "prep", "encode", "sampling", "vae", "save") else "load"}
    if snap["stage"] == "sampling" and snap.get("max"):
        info.update(passo=snap.get("step", 0), total=snap["max"])
        sampling = snap["durations"].get("sampling", 0)
        if snap.get("step"):
            info["restante_s"] = int((snap["max"] - snap["step"]) * sampling / snap["step"] + (expected_vae or 0))
    return info


def ticker(report, expected_total, expected_vae=None, min_interval=4.0, heartbeat=15.0):
    """Envia progresso ao mudar de etapa/passo (no máx. a cada 4 s) e um batimento a cada 15 s."""
    state = {"at": -1e9, "key": None}

    def tick(elapsed, snap=None):
        pct, message = progress_message(snap, elapsed, expected_total, expected_vae)
        key = (snap or {}).get("stage"), (snap or {}).get("step")
        changed = key != state["key"]
        if (changed and elapsed - state["at"] >= min_interval) or elapsed - state["at"] >= heartbeat:
            state.update(at=elapsed, key=key)
            report(pct, message, None, progress_info(snap, expected_vae))  # também renova a reserva e detecta cancelamento
    return tick


def generate(comfy, history, report, prompt, seed, width, height, steps, init_name=None, denoise=1.0, stage="Gerando"):
    workflow = comfy.build(prompt, seed, width, height, init_name, denoise, steps)
    steps = steps or workflow_steps(workflow)
    tick = ticker(report, history.expected(width, height, steps), history.expected(width, height, steps, "vae"))
    image, gen = comfy.run(workflow, tick)
    history.add(width, height, gen["comfy_s"], steps)
    if gen.get("vae_s"):
        history.add(width, height, gen["vae_s"], steps, "vae")
    gen["memoria"] = comfy.after_job()
    return image, gen


def process_free_image(client, job, comfy, history):
    job_id, payload = job["id"], job["payload"]
    width, height = payload["size"]
    report = make_reporter(client, job_id)
    timings, t0 = {}, time.time()
    log.info("tarefa %s: free_image %dx%d %s passos=%s", job_id[:8], width, height, payload.get("render_mode"),
             payload.get("steps") or "workflow")

    init_name = None
    if payload.get("render_mode") == "img2img" and payload.get("init_asset_id"):
        report(5, "Baixando a imagem de referência", None, {"etapa": "download"})
        started = time.time()
        init_bytes = client.asset(payload["init_asset_id"])
        timings["download_s"] = time.time() - started
        if comfy:
            started = time.time()
            init_name = comfy.upload_image(_to_png(init_bytes), f"sg_{job_id[:8]}.png")
            timings["upload_ref_s"] = time.time() - started

    if comfy:
        timings.update(comfy.memory())
        report(8, "Iniciando no Acer", None, {"etapa": "load"})
        image, gen = generate(comfy, history, report, payload["prompt"], payload["seed"], width, height,
                              payload.get("steps"), init_name, payload.get("denoise", 1.0))
        memory_note = gen.pop("memoria", "")
        timings.update(gen)
    else:
        image, memory_note = placeholder_image(width, height, payload["prompt"]), ""
    composed = compose_texts(payload, image, timings, report)
    timings["total_worker_s"] = time.time() - t0
    report(97, "Enviando a imagem", timings, {"etapa": "send"})
    if composed:
        result = client.complete_composed(job_id, _art_jpeg(image), *composed)
    else:
        result = client.complete_image(job_id, image)
    log.info("tarefa %s concluída em %.1fs (asset %s; %s) %s", job_id[:8], timings["total_worker_s"],
             result.get("asset_id", "")[:8], memory_note, json.dumps({k: round(v, 1) for k, v in timings.items()}))


def _art_jpeg(data):
    """Arte limpa em JPEG de alta qualidade (menor para enviar; usada em ajustes futuros)."""
    from PIL import Image  # noqa: PLC0415

    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    return out.getvalue()


def compose_texts(payload, image, timings, report):
    """v2.3: aplica os textos aqui (CPU do Acer) e valida com leitura visual local (OCR), se instalado.

    Sem o plano de textos, com SG_AGENT_COMPOSE=0 ou em qualquer erro, devolve None e o servidor compõe.
    """
    plan = (payload.get("spec") or {}).get("text_plan")
    if not plan or not plan.get("blocos") or os.getenv("SG_AGENT_COMPOSE", "1") == "0":
        return None
    try:
        from studio import typography  # noqa: PLC0415

        report(96, "Aplicando os textos", timings, {"etapa": "texto"})
        started = time.time()
        final, result = typography.compose(image, plan)
        timings["texto_s"] = time.time() - started
        ocr = result.get("ocr") or {}
        if ocr.get("tempo_s") is not None:
            timings["ocr_s"] = ocr["tempo_s"]
        log.info("textos aplicados em %.1fs (validação %s; leitura visual %s)", timings["texto_s"],
                 "ok" if result.get("ok") else "com avisos", ocr.get("ok", "indisponível"))
        return final, result
    except Exception as exc:  # noqa: BLE001 — o servidor compõe no lugar
        log.warning("composição local falhou (%s); o servidor vai aplicar os textos", exc)
        return None


def process_ad(client, job, comfy, history):
    job_id, payload = job["id"], job["payload"]
    briefing = payload["briefing"]
    report = make_reporter(client, job_id)
    timings, t0 = {}, time.time()
    log.info("tarefa %s: encarte, %d produto(s), formato %s", job_id[:8], len(briefing["produtos"]), briefing["formato"])

    report(5, "Baixando fotos e logo", None, {"etapa": "download"})
    started = time.time()
    assets = {asset_id: client.asset(asset_id) for asset_id in payload.get("asset_ids", [])}
    timings["download_s"] = time.time() - started

    scene = None
    if comfy:
        width, height = payload["scene_size"]
        timings.update(comfy.memory())
        report(10, "Gerando cenário com IA", None, {"etapa": "load"})
        scene, gen = generate(comfy, history, report, payload["scene_prompt"], random.randint(1, 2**31 - 1),
                              width, height, payload.get("steps"), stage="Gerando cenário")
        gen.pop("memoria", None)
        timings.update(gen)

    report(90, "Montando encarte com fotos e preços", None, {"etapa": "compose"})
    started = time.time()
    flyer = compose_flyer(
        briefing,
        scene_bytes=scene,
        assets=assets,
        logo_bytes=assets.get(briefing.get("logo_asset_id")),
        licenses=payload.get("licenses"),
    )
    timings["compose_s"] = time.time() - started
    timings["total_worker_s"] = time.time() - t0
    report(97, "Enviando o encarte", timings, {"etapa": "send"})
    result = client.complete_image(job_id, flyer)
    log.info("tarefa %s concluída (asset %s)", job_id[:8], result.get("asset_id", "")[:8])


def process(client, job, comfy, history=None):
    history = history or LocalTimings()
    if job["payload"].get("gen_type") == "free_image":
        return process_free_image(client, job, comfy, history)
    return process_ad(client, job, comfy, history)


def bench(comfy, sizes, steps_list, runs, prompt, free_between=False):
    """Mede tempos reais no ComfyUI (sem servidor). Marca execução fria (com carga do modelo) e quente."""
    results = []
    for width, height in sizes:
        for steps in steps_list:
            for i in range(runs):
                before = comfy.memory()
                workflow = comfy.build(prompt, random.randint(1, 2**31 - 1), width, height, steps=steps)
                started = time.time()
                image, timings = comfy.run(workflow, lambda _elapsed, _snap=None: None)
                tag = f"{width}x{height}_{steps or workflow_steps(workflow)}p_{i + 1}"
                out = os.path.join(HERE, f"bench_{tag}.png")
                with open(out, "wb") as fh:
                    fh.write(image)
                row = {"tamanho": f"{width}x{height}", "passos": steps or workflow_steps(workflow), "execucao": i + 1,
                       "parede_s": round(time.time() - started, 1),
                       **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in timings.items()},
                       **{f"antes_{k}": round(v) for k, v in before.items()}, "arquivo": out}
                log.info("bench %s", json.dumps(row, ensure_ascii=False))
                results.append(row)
                if free_between:
                    comfy.free_memory()
    path = os.path.join(HERE, "bench_resultado.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    log.info("resultado salvo em %s", path)
    return results


BENCH_PROMPTS = [
    "an astronaut standing in a misty cinematic forest at dawn, volumetric light, photorealistic, no text",
    "a cozy coffee shop interior with warm lights and wooden tables, people reading, soft bokeh, no text",
    "a red vintage car parked on a coastal road at sunset, golden hour, film photography, no text",
    "a bowl of fresh tropical fruits on a marble kitchen counter, studio product photo, soft shadows, no text",
    "a modern minimalist living room with large windows and plants, real estate photography, bright, no text",
]


def bench_prompts(comfy, width, height, steps, repeat, devices):
    """Cenário real: um prompt diferente por geração (o condicionamento nunca é reaproveitado entre prompts).

    Para cada dispositivo do codificador: 1 aquecimento (descartado) + 5 prompts distintos e, com --repeat,
    o último prompt de novo (mostra o ganho quando o pedido é idêntico, como em "outra versão").
    """
    results = []
    for device in devices:
        warm = comfy.build("warm-up image of a simple gray cube, no text", 1, width, height, steps=steps, clip_device=device)
        comfy.run(warm, lambda _e, _s=None: None)
        runs = [(p, "novo") for p in BENCH_PROMPTS] + ([(BENCH_PROMPTS[-1], "repetido")] if repeat else [])
        for i, (prompt, scenario) in enumerate(runs, 1):
            before = comfy.memory()
            workflow = comfy.build(prompt, random.randint(1, 2**31 - 1), width, height, steps=steps, clip_device=device)
            started = time.time()
            image, timings = comfy.run(workflow, lambda _e, _s=None: None)
            out = os.path.join(HERE, f"bench_prompt{i}_{device}_{width}x{height}.png")
            with open(out, "wb") as fh:
                fh.write(image)
            row = {"tamanho": f"{width}x{height}", "passos": steps or workflow_steps(workflow), "execucao": i,
                   "cenario": scenario, "codificador": device, "parede_s": round(time.time() - started, 1),
                   **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in timings.items()},
                   **{f"antes_{k}": round(v) for k, v in before.items()}, "prompt": prompt[:60], "arquivo": out}
            log.info("bench %s", json.dumps(row, ensure_ascii=False))
            results.append(row)
    path = os.path.join(HERE, "bench_prompts_resultado.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    for device in devices:
        for scenario in ("novo", "repetido"):
            rows = [r for r in results if r["codificador"] == device and r["cenario"] == scenario]
            if rows:
                med = {k: statistics.median([r.get(k) or 0 for r in rows])
                       for k in ("comfy_s", "encode_s", "preparo_amostragem_s", "sampling_s", "vae_s")}
                log.info("RESUMO codificador=%s cenario=%s (%d): %s", device, scenario, len(rows),
                         " ".join(f"{k}={v:.1f}" for k, v in med.items()))
    log.info("resultado salvo em %s", path)
    return results


def make_client():
    for name in ("SG_BASE_URL", "SG_WORKER_ID", "SG_WORKER_SECRET"):
        if not os.getenv(name):
            raise SystemExit(f"Defina {name} (veja workers/.env.example)")
    return WorkerClient(os.environ["SG_BASE_URL"], os.environ["SG_WORKER_ID"], os.environ["SG_WORKER_SECRET"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-comfy", action="store_true")
    parser.add_argument("--bench", help="tamanhos, ex.: 640x640,768x960")
    parser.add_argument("--steps", default="", help="passos a testar no --bench, ex.: 6,9 (vazio = do workflow)")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--free-between", action="store_true", help="--bench: liberar memória entre execuções (mede carga a frio)")
    parser.add_argument("--report", action="store_true", help="--bench: enviar o resultado ao SiteGenius (estimativas da tela)")
    parser.add_argument("--bench-prompts", action="store_true", help="5 prompts diferentes (cenário real)")
    parser.add_argument("--size", default="640x640", help="--bench-prompts: tamanho")
    parser.add_argument("--repeat", action="store_true", help="--bench-prompts: repetir o último prompt (pedido idêntico)")
    parser.add_argument("--clip-device", default="default", help="--bench-prompts: default,cpu para comparar")
    parser.add_argument("--prompt", default="an astronaut standing in a misty cinematic forest at dawn, volumetric light, "
                                            "photorealistic, highly detailed, no text")
    args = parser.parse_args(argv)

    load_env_file(os.path.join(HERE, ".env"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    comfy = None
    if not args.no_comfy:
        comfy = Comfy(
            os.getenv("COMFY_URL", "http://127.0.0.1:8188"),
            os.getenv("SG_COMFY_WORKFLOW", os.path.join(HERE, "comfyui_workflow.json")),
            int(os.getenv("SG_SCENE_TIMEOUT", "900")),
        )
    if args.bench_prompts:
        if not comfy:
            raise SystemExit("--bench-prompts precisa do ComfyUI")
        width, height = (int(n) for n in args.size.lower().split("x"))
        steps = int(args.steps) if args.steps.strip().isdigit() else None
        results = bench_prompts(comfy, width, height, steps, args.repeat, [d.strip() for d in args.clip_device.split(",") if d.strip()])
        if args.report:
            sent = make_client().report_benchmarks(results)
            log.info("benchmark enviado ao SiteGenius: %s linha(s) aceitas", sent.get("aceitas"))
        return 0
    if args.bench:
        if not comfy:
            raise SystemExit("--bench precisa do ComfyUI")
        sizes = [tuple(int(n) for n in item.lower().split("x")) for item in args.bench.split(",")]
        steps_list = [int(s) for s in args.steps.split(",") if s.strip()] or [None]
        results = bench(comfy, sizes, steps_list, args.runs, args.prompt, args.free_between)
        if args.report:
            sent = make_client().report_benchmarks(results)
            log.info("benchmark enviado ao SiteGenius: %s linha(s) aceitas", sent.get("aceitas"))
        return 0

    client = make_client()
    if args.check:
        if comfy:
            stats = comfy.check()
            log.info("ComfyUI OK: %s", json.dumps(stats.get("system", {}))[:200])
            to_img2img(comfy.build("teste", 1, 64, 64, steps=4), "teste.png", 0.7)
            log.info("Workflow OK para txt2img, img2img e troca de passos (padrão do workflow: %s).",
                     workflow_steps(comfy.build("teste", 1, 64, 64)))
            try:
                import websocket  # noqa: F401
                log.info("websocket-client instalado: progresso por etapa/passo ativo.")
            except ImportError:
                log.warning("websocket-client ausente: rode 'pip install websocket-client' para progresso por passo.")
        log.info("Configuração OK (nenhuma tarefa buscada).")
        return 0

    history = LocalTimings()
    poll = int(os.getenv("SG_POLL_SECONDS", "10"))
    log.info("agente iniciado; buscando tarefas em %s a cada %ss (modelo mantido entre tarefas)", os.environ["SG_BASE_URL"], poll)
    while True:
        job = None
        try:
            job = client.claim()
            if job:
                process(client, job, comfy, history)
        except JobCanceled:
            log.info("tarefa cancelada pelo usuário")
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 — o agente nunca deve morrer por uma tarefa
            log.exception("erro: %s", exc)
            if job:
                try:
                    client.fail(job["id"], f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    log.warning("não foi possível reportar a falha")
        if args.once:
            return 0
        if not job:
            time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
