"""Converte o workflow do Klein (exportado do ComfyUI em formato de API) no MODELO que o agente atual do Acer usa:
marca {{PROMPT}}, {{WIDTH}}, {{HEIGHT}} e {{SEED}} pelos tipos dos nós e acrescenta a super-resolução na GPU
(Real-ESRGAN x4 + redução a ~1,4 MP com ImageScaleBy, o maior tamanho que cabe no envio de 4 MB da fila atual).

Uso: python preparar_workflow_klein.py klein_api.json klein_modelo_agente.json [--sem-ampliacao] [--ate-1-4mp]
Padrão: amplia até o tamanho FINAL pedido ({{FINAL_WIDTH}} x {{FINAL_HEIGHT}}), com envio direto ao Cloudinary.
"""
import json
import sys

UPSCALE_MODEL = "RealESRGAN_x4plus.pth"
SCALE_BY = 0.29  # depois do x4 do Real-ESRGAN: ~1,4 MP para gerações de ~1 MP


def main(src, dst, upscale=True, final=True):
    wf = json.load(open(src, encoding="utf-8"))
    texts = [(k, n) for k, n in wf.items() if isinstance(n.get("inputs", {}).get("text"), str)]
    if not texts:
        raise SystemExit("workflow sem nó de texto")
    k, node = max(texts, key=lambda kn: len(kn[1]["inputs"]["text"]))
    node["inputs"]["text"] = "{{PROMPT}}"
    sized = 0
    for n in wf.values():
        inp = n.get("inputs", {})
        if isinstance(inp.get("width"), int) and isinstance(inp.get("height"), int):
            inp["width"], inp["height"] = "{{WIDTH}}", "{{HEIGHT}}"
            sized += 1
        for s in ("seed", "noise_seed"):
            if isinstance(inp.get(s), int):
                inp[s] = "{{SEED}}"
    if not sized:
        raise SystemExit("workflow sem nó de tamanho (width/height)")
    save = next((kk for kk, n in wf.items() if n.get("class_type") == "SaveImage"), None)
    if not save:
        raise SystemExit("workflow sem SaveImage")
    if upscale:
        src_img = wf[save]["inputs"]["images"]
        nid = max(int(x) for x in wf if x.isdigit()) + 1
        wf[str(nid)] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}}
        wf[str(nid + 1)] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": [str(nid), 0], "image": src_img}}
        if final:
            # tamanho FINAL exato pedido pelo cliente (4K inclusive); o agente envia direto ao Cloudinary o que for grande
            wf[str(nid + 2)] = {"class_type": "ImageScale",
                                "inputs": {"image": [str(nid + 1), 0], "upscale_method": "lanczos", "width": "{{FINAL_WIDTH}}",
                                           "height": "{{FINAL_HEIGHT}}", "crop": "center"}}
        else:
            # sem envio direto: ~1,4 MP (cabe no envio de 4 MB). ImageScaleBy tem as mesmas entradas há anos.
            wf[str(nid + 2)] = {"class_type": "ImageScaleBy",
                                "inputs": {"image": [str(nid + 1), 0], "upscale_method": "lanczos", "scale_by": SCALE_BY}}
        wf[save]["inputs"]["images"] = [str(nid + 2), 0]
    wf[save]["inputs"]["filename_prefix"] = "sglab_klein"
    json.dump(wf, open(dst, "w", encoding="utf-8"), indent=1)
    print("modelo do agente gravado em", dst, "(com super-resolução)" if upscale else "")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--sem-ampliacao" not in sys.argv, "--ate-1-4mp" not in sys.argv)
