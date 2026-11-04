"""Rodada 2 do laboratório Klein: prompt de cenário adaptado ao FLUX.2 Klein (cena.prompt_klein) nos casos que tiveram
texto inventado ou fundo vazio, e o 4K de verdade (tamanho final no próprio workflow + envio direto ao Cloudinary).
  python lab/lab_klein2.py enviar | coletar"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SP = Path(r"C:\Users\windows\AppData\Local\Temp\claude\C--Users-windows-Downloads-sitegenius-rebuild\bf29e511-e568-4d8f-af51-6cc897b0dba5\scratchpad")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(SP / "klein_cmp"))
import lab_jobs  # noqa: E402

CASES = ["01_blackfriday_tech", "02_supermercado", "05_estetica", "06_farmacia_fotos", "09_futurista_3d"]


def enviar():
    import klein_cmp as K  # noqa: PLC0415

    from studio.livre import cena, formato  # noqa: PLC0415
    plans = json.load(open(K.PLANS, encoding="utf-8"))
    c = lab_jobs.conn()
    uid = lab_jobs.lab_user(c)
    ts = int(time.time())
    for n, name in enumerate(CASES):
        lab_id = f"{name}_B2"
        if c.one("SELECT id FROM sg_jobs WHERE prompt_original = ?", (lab_id,)):
            continue
        b, plan, solved = K._solved(name, plans[name]["texto"])
        gen = formato.generation_plan(tuple(b["tamanho"]))
        prompt = cena.prompt_klein(plan, solved, b)
        payload = {"version": 2, "gen_type": "free_image", "prompt": prompt, "negative_prompt": "", "seed": 2000 + n, "size": list(gen["geracao"]),
                   "final_size": list(gen["final"]), "steps": None, "render_mode": "txt2img", "init_asset_id": None, "denoise": 1.0,
                   "asset_ids": [], "spec": {"laboratorio": lab_id}}
        c.run("INSERT INTO sg_jobs (id, user_id, conversation_id, kind, status, payload, status_message, schema_version, gen_type, "
              "prompt_original, prompt_visual, seed, render_mode, created_at, updated_at) "
              "VALUES (?, ?, NULL, 'render', 'queued', ?, 'Na fila do laboratório', 2, 'free_image', ?, ?, ?, 'txt2img', ?, ?)",
              (str(uuid.uuid4()), uid, json.dumps(payload, ensure_ascii=False), lab_id, prompt[:4000], 2000 + n, ts + n, ts + n))
        print(lab_id, gen["geracao"], "->", gen["final"])
    c.commit()


def coletar():
    """Como lab_jobs.coletar, mas também baixa do Cloudinary (envio direto das imagens grandes)."""
    import os  # noqa: PLC0415
    import re  # noqa: PLC0415
    dns = Path(r"C:\Users\Public\dns-ecommerce\.env").read_text(encoding="utf-8-sig")
    cl = {k: re.search(rf"^{k}=(.+)$", dns, re.M).group(1).strip().strip("\"'") for k in
          ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")}
    os.environ["SG_CLOUDINARY_URL"] = f"cloudinary://{cl['CLOUDINARY_API_KEY']}:{cl['CLOUDINARY_API_SECRET']}@{cl['CLOUDINARY_CLOUD_NAME']}"
    from studio import storage  # noqa: PLC0415
    c = lab_jobs.conn()
    for j in c.all("SELECT * FROM sg_jobs WHERE prompt_original LIKE ? ORDER BY created_at", ("%_B2",)):
        out = lab_jobs.FILA / "saida" / j["prompt_original"]
        if (out / "metricas.json").exists() or j["status"] not in ("done", "failed"):
            continue
        out.mkdir(parents=True, exist_ok=True)
        res = json.loads(j["result"] or "{}")
        m = {"id": j["prompt_original"], "status": j["status"], "erro": j.get("error"), "envio": res.get("envio", "normal"),
             "etapas": json.loads(j.get("timings") or "{}"), "geracao": json.loads(j["payload"])["size"]}
        if res.get("asset_id"):
            a = c.one("SELECT data, storage_key, width, height, mime FROM sg_assets WHERE id = ?", (res["asset_id"],))
            data = a["data"] or storage.get(a["storage_key"], a["mime"])
            (out / "imagem.png").write_bytes(data)
            m["resultado"] = [a["width"], a["height"]]
        (out / "metricas.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(j["prompt_original"], m.get("resultado"), m["envio"], m["status"])


if __name__ == "__main__":
    {"enviar": enviar, "coletar": coletar}[sys.argv[1]]()
