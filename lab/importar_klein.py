"""Importa as imagens do Klein geradas direto no ComfyUI do Acer (C:\\IA-Imagens\\ComfyUI\\output\\klein_lab_*, copiadas
para a PE0) e roda a próxima etapa inteira:
  1) organiza em deploy/acer_klein_lab/fila/saida/<id>/imagem.png (id = 01_blackfriday_tech_A ...);
  2) compõe a abordagem B sobre o cenário do Klein, mede os textos da A (OCR) e monta as folhas lado a lado;
  3) comparação técnica dos compositores (nosso x Pango/Cairo) sobre as imagens do Klein, e fila do LayerStyle.
Uso: python lab/importar_klein.py <pasta_com_os_arquivos_copiados>"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILA = ROOT / "deploy" / "acer_klein_lab" / "fila"
SP = Path(r"C:\Users\windows\AppData\Local\Temp\claude\C--Users-windows-Downloads-sitegenius-rebuild\bf29e511-e568-4d8f-af51-6cc897b0dba5\scratchpad")
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def organize(src):
    ids = [p.stem for p in (FILA / "entrada").glob("*.json")]
    found = {}
    for f in sorted(Path(src).rglob("*")):
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        name = f.name.lower()
        hit = next((i for i in sorted(ids, key=len, reverse=True) if i.lower() in name), None)
        if not hit:  # nomes curtos (ex.: klein_lab_01_A_00001_.png)
            m = re.search(r"(\d{2})\D*?_([ab])(?:_|\b|\.)", name)
            if m:
                hit = next((i for i in ids if i.startswith(m.group(1) + "_") and i.endswith("_" + m.group(2).upper())), None)
        if hit and hit not in found:
            found[hit] = f
    for i, f in found.items():
        d = FILA / "saida" / i
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, d / "imagem.png")
        job = json.loads((FILA / "entrada" / f"{i}.json").read_text(encoding="utf-8"))
        from PIL import Image  # noqa: PLC0415
        with Image.open(f) as im:
            size = list(im.size)
        (d / "metricas.json").write_text(json.dumps({"id": i, "origem": str(f), "geracao": [job["gen_w"], job["gen_h"]], "resultado": size,
                                                     "fonte": "ComfyUI do Acer (direto)"}, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = sorted(set(ids) - set(found))
    print(f"importadas {len(found)} de {len(ids)}" + (f"; faltaram: {missing}" if missing else ""))
    return found


def main(src):
    organize(src)
    cmp_ = SP / "klein_cmp" / "klein_cmp.py"  # a cópia de trabalho, junto das artes da arquitetura atual
    subprocess.run([str(PY), str(cmp_), "compor"], check=False)
    subprocess.run([str(PY), str(cmp_), "folha"], check=False)


if __name__ == "__main__":
    main(sys.argv[1])
