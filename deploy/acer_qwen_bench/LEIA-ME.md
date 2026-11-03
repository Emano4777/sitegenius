# Teste isolado do Qwen3-8B no Acer (autorizado em 03/11/2026)

Objetivo: medir o Qwen3-8B (Q4_K_M, ~5 GB) como diretor criativo nos MESMOS 15 briefings do Gemma 4B.
O teste não substitui nada: o Gemma continua em produção e o agente/ComfyUI/workflow não são alterados.

Regras:
- Nunca com o ComfyUI ligado (os dois modelos não cabem juntos na RTX 4050 de 6 GB). O script recusa rodar se o
  ComfyUI (127.0.0.1:8188) ou o `workers.acer_agent` estiverem ativos.
- Pasta própria, fora do SiteGenius: `C:\IA-Imagens\qwen-bench`. Apagar a pasta desfaz tudo (nada é instalado no sistema).
- O servidor do modelo escuta só em 127.0.0.1:8791 e é encerrado no fim do teste.

## Arquivos (copiar de `sitegenius-rebuild\deploy\acer_qwen_bench` para `C:\IA-Imagens\qwen-bench`)
| Arquivo | SHA-256 (16) |
|---|---|
| `qwen_bench.py` | `9214e1c18be764b6` |
| `mensagens.json` | `ef4aedadb1b77793` |

## Passos (PowerShell)
```powershell
cd C:\IA-Imagens\qwen-bench
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py qwen_bench.py --baixar          # llama.cpp b12074 CUDA 12.4 (~680 MB) + Qwen3-8B-Q4_K_M.gguf (5,03 GB)
```
Janela de teste (~10–20 min; os pedidos de imagem do site esperam na fila e são atendidos depois):
1. Parar o agente (`workers.acer_agent`) e fechar o ComfyUI.
2. `& $py qwen_bench.py`
3. Religar o ComfyUI e o agente do jeito normal; conferir com `& $py -m workers.acer_agent --check`.

Se o 8B ficar lento demais (menos de 3 tokens/s) ou não carregar: `& $py qwen_bench.py --baixar --com-4b` e
`& $py qwen_bench.py --modelo Qwen3-4B-Q4_K_M.gguf` (grava por cima de `resultados_qwen.json`; renomeie o do 8B antes).

## Devolver
Copiar `C:\IA-Imagens\qwen-bench\resultados_qwen.json` (e os `servidor_ngl*.log`) para
`C:\Users\windows\Downloads\sitegenius-rebuild\deploy\acer_qwen_bench\` na PE0. A comparação visual (mesmo
resolvedor, compositor e validação para os dois modelos) é feita na PE0.

## Rollback
Apagar `C:\IA-Imagens\qwen-bench`. Nada mais foi alterado.
