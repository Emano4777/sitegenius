# Atualização do Acer — agente v2.3 + Q3 (02/11/2026)

O que muda no Acer:
- **Agente v2.3:** aplica os textos na própria máquina (CPU), incluindo superfícies, e confere o resultado com leitura
  visual local (OCR). Sem o OCR instalado, ou com `SG_AGENT_COMPOSE=0`, o servidor aplica os textos como hoje.
- **Q3:** passa a ser o codificador definitivo (só `clip_name` no workflow do agente; o ComfyUI não é tocado).

## 1. Arquivos a copiar (de `sitegenius-rebuild` para `C:\IA-Imagens\sitegenius`)
| Arquivo | SHA-256 (16) |
|---|---|
| `workers\acer_agent.py` | `7b61a5b6610334be` |
| `studio\worker_client.py` | `8135c894ca5a015d` |
| `studio\typography.py` | `6daee12db7d0c0b3` |
| `studio\textplan.py` | `db42b3bfff3f077d` |
| `studio\surfaces.py` | `c5fc2e12c1fd8b29` |
| `studio\ocr.py` | `7ad81dd99d245ae7` |
| `studio\creative.py` | `be214124805688c3` |
| `studio\edits.py` | `db369b453a12c345` |
| `studio\fonts\` (pasta inteira: 13 fontes + licenças) | — |

O mais simples é copiar a pasta `studio\` inteira. Não mexer em `workers\.env` nem no ComfyUI.

## 2. Dependências (CPU; não usam a GPU)
```powershell
cd C:\IA-Imagens\sitegenius
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py -m pip install "numpy>=1.26" rapidocr-onnxruntime
& $py -c "from studio import ocr; print(ocr.status())"   # deve mostrar disponivel: True
```
O RapidOCR usa os modelos PP-OCR do PaddleOCR em ONNX Runtime, na CPU, com cerca de 110 MB de RAM e 1 a 3 s por imagem.
Se a RAM ficar apertada, `SG_OCR=0` desliga só a leitura visual.

## 3. Q3 definitivo (depois de validar, ver `docs/TEXTOS_NA_IMAGEM.md`)
1. Fazer backup: copiar `workers\comfyui_workflow.json` para `workers\comfyui_workflow.iq2.json`.
2. No `workers\comfyui_workflow.json`, nó `CLIPLoaderGGUF`: `"clip_name": "Qwen3-4B-Q3_K_S.gguf"` (nada mais muda).
3. Reiniciar o agente do jeito normal (sem a variável temporária `SG_COMFY_WORKFLOW`).
4. `& $py -m workers.acer_agent --check`.

**Rollback:** voltar o arquivo `comfyui_workflow.iq2.json` para `comfyui_workflow.json` e reiniciar o agente.
Para o agente v2.3, recolocar os arquivos antigos ou definir `SG_AGENT_COMPOSE=0`.

## 4. Conferência depois de reiniciar
- No admin do Studio, em "Atividade recente", a peça com texto deve mostrar "textos ok … OCR ok (Xs) … no acer".
- No log do agente: `textos aplicados em X s (validação ok; leitura visual True)`.
