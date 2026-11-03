"""Vigia a fila do laboratório: conforme o Acer entrega, coleta imagens e métricas; quando os 20 terminarem, roda o
compositor (abordagem B), mede a abordagem A e monta as folhas. Uso: python lab/vigiar_fila.py"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab"))
import lab_jobs  # noqa: E402

PY = ROOT / ".venv" / "Scripts" / "python.exe"
CMP = Path(r"C:\Users\windows\AppData\Local\Temp\claude\C--Users-windows-Downloads-sitegenius-rebuild\bf29e511-e568-4d8f-af51-6cc897b0dba5\scratchpad\klein_cmp\klein_cmp.py")
last = None
while True:
    c = lab_jobs.conn()
    st = {r["status"]: r["n"] for r in c.all("SELECT status, COUNT(*) AS n FROM sg_jobs WHERE kind = 'render' GROUP BY status")}
    c.close()
    if st != last:
        print("fila do laboratório:", st, flush=True)
        last = st
        lab_jobs.coletar()
    if not st.get("queued") and not st.get("claimed") and not st.get("running"):
        print("lote concluído: compondo e montando as folhas", flush=True)
        subprocess.run([str(PY), str(CMP), "compor"])
        subprocess.run([str(PY), str(CMP), "folha"])
        print("FIM", flush=True)
        break
    time.sleep(30)
