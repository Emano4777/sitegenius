"""Trabalhos do laboratório na fila ISOLADA (schema sitegenius_lab), no mesmo formato que o chat cria (free_image),
para o mesmo agente do Acer processar. Depois coleta imagens e métricas (tempos por etapa, memória) do banco do
laboratório para deploy/acer_klein_lab/fila/saida/<id>/, que é o que o comparador (lab/klein_cmp.py compor) lê.
  python lab/lab_jobs.py enviar     # as 20 solicitações de fila/entrada (10 pedidos x abordagens A e B)
  python lab/lab_jobs.py status
  python lab/lab_jobs.py coletar"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab_setup import SECRETS  # noqa: E402

from studio import db  # noqa: E402

FILA = ROOT / "deploy" / "acer_klein_lab" / "fila"


def conn():
    return db.Conn(db.Database((SECRETS / "lab_db_url.txt").read_text(encoding="utf-8").strip()))


def lab_user(c):
    return c.one("SELECT id FROM sg_users WHERE email = ?", ("lab.klein@sitegenius.com.br",))["id"]


def enviar():
    c = conn()
    uid = lab_user(c)
    ts = int(time.time())
    n = 0
    for f in sorted((FILA / "entrada").glob("*.json")):
        job = json.loads(f.read_text(encoding="utf-8"))
        if c.one("SELECT id FROM sg_jobs WHERE prompt_original = ?", (job["id"],)):
            continue  # já enviado
        payload = {"version": 2, "gen_type": "free_image", "prompt": job["prompt"], "negative_prompt": "", "seed": job["seed"],
                   "size": [job["gen_w"], job["gen_h"]], "steps": None, "render_mode": "txt2img", "init_asset_id": None,
                   "denoise": 1.0, "asset_ids": [], "spec": {"laboratorio": job["id"], "final": [job["final_w"], job["final_h"]]}}
        c.run("INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, gen_type, "
              "prompt_original, prompt_visual, seed, render_mode, created_at, updated_at) "
              "VALUES (?, ?, NULL, 'render', 'queued', ?, 'Na fila do laboratório', 2, 'free_image', ?, ?, ?, 'txt2img', ?, ?)",
              (str(uuid.uuid4()), uid, json.dumps(payload, ensure_ascii=False), job["id"], job["prompt"][:4000], job["seed"], ts + n, ts + n))
        n += 1
    c.commit()
    print("enviados:", n)


def status():
    c = conn()
    for r in c.all("SELECT status, COUNT(*) AS n FROM sg_jobs GROUP BY status"):
        print(r["status"], r["n"])


def coletar():
    c = conn()
    got = 0
    for j in c.all("SELECT * FROM sg_jobs WHERE kind = 'render' ORDER BY created_at"):
        out = FILA / "saida" / j["prompt_original"]
        if (out / "metricas.json").exists():
            continue
        if j["status"] not in ("done", "failed"):
            continue
        out.mkdir(parents=True, exist_ok=True)
        res = json.loads(j["result"] or "{}")
        m = {"id": j["prompt_original"], "status": j["status"], "erro": j.get("error"),
             "fila_s": (j["started_at"] or j["created_at"]) - j["created_at"] if j.get("started_at") else None,
             "total_s": (j["finished_at"] - j["started_at"]) if j.get("finished_at") and j.get("started_at") else None,
             "etapas": json.loads(j.get("timings") or "{}"), "geracao": json.loads(j["payload"])["size"]}
        if res.get("asset_id"):
            a = c.one("SELECT data, width, height, mime FROM sg_assets WHERE id = ?", (res["asset_id"],))
            if a and a.get("data"):
                (out / "imagem.png").write_bytes(a["data"])
                m["resultado"] = [a["width"], a["height"]]
        (out / "metricas.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        got += 1
    print("coletados:", got)


if __name__ == "__main__":
    {"enviar": enviar, "status": status, "coletar": coletar}[sys.argv[1]]()
