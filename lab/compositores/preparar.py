"""Comparação técnica dos compositores: MESMO plano e MESMA imagem.
  python preparar.py <fonte_dos_cenarios> <casos...>
fonte_dos_cenarios: atual (Z-Image) ou klein_B (cenário do Klein ampliado). Gera:
  nosso/<caso>/arte.png  (nosso compositor)      export/<caso>/composicao.json (+cenário, fotos, fontes)
  fila_ls/entrada/<caso> (para o LayerStyle no Acer)"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SP, "klein_cmp"))
import exportar  # noqa: E402
import klein_cmp as K  # noqa: E402
from PIL import Image  # noqa: E402

from studio.livre import cena, pipeline  # noqa: E402

FILA_LS = os.path.join(K.REPO, "deploy", "acer_klein_lab", "fila_ls", "entrada")


def main(src, names):
    plans = json.load(open(K.PLANS, encoding="utf-8"))
    ocr = cena.rapidocr_fn()
    tempos = {}
    for name in names:
        b, photos, _ = K.briefing_of(name)
        sc = os.path.join(SP, "klein_cmp", src, name, "cenario.jpg")
        if not os.path.exists(sc):
            print(name, "sem cenário em", src)
            continue
        od = os.path.join(HERE, f"nosso_{src}", name)
        t = time.time()
        r = pipeline.run(b, photos, lambda m: plans[name]["texto"], lambda p, a: Image.open(sc), ocr=ocr, workdir=od, max_scene=1)
        tempos[name] = {"nosso_s": round(time.time() - t, 1), "finais": [p[0] for p in r["problemas_finais"]]}
        info = {i: pipeline.photo_info(d) for i, d in photos.items()}
        ex = os.path.join(HERE, f"export_{src}", name)
        spec_p = exportar.export(r, b, info, os.path.abspath(os.path.join(od, "cenario.jpg")), ex)
        spec = json.load(open(spec_p, encoding="utf-8"))
        # pacote autocontido para o LayerStyle: cenário, fotos e fontes junto da composição
        dst = os.path.join(FILA_LS, f"{name}_{src}")
        os.makedirs(dst, exist_ok=True)
        shutil.copy(spec["cenario"], os.path.join(dst, os.path.basename(spec["cenario"])))
        for el in spec["elementos"]:
            for k in ("fonte", "fonte_preco", "foto"):
                if el.get(k) and os.path.exists(el[k]):
                    shutil.copy(el[k], os.path.join(dst, os.path.basename(el[k])))
        shutil.copy(spec_p, os.path.join(dst, "composicao.json"))
        print(name, tempos[name], flush=True)
    json.dump(tempos, open(os.path.join(HERE, f"tempos_nosso_{src}.json"), "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
