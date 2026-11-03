# Teste isolado do Qwen3-4B no Acer (autorizado em 03/11/2026)

Mesmo pacote e mesmos 15 briefings do teste do 8B. Nada do SiteGenius é alterado: agente, ComfyUI, workflow e
modelos continuam como estão; o Gemma de produção não muda. Pasta: `C:\IA-Imagens\qwen-bench` (a mesma do 8B).

Atualize só o script (o `mensagens.json` é o mesmo):
| Arquivo | SHA-256 (16) |
|---|---|
| `qwen_bench.py` | `ad1e07a3c18303bf` |
| `mensagens.json` | `ef4aedadb1b77793` |

```powershell
cd C:\IA-Imagens\qwen-bench
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py qwen_bench.py --baixar --com-4b        # baixa só o que falta: Qwen3-4B-Q4_K_M.gguf (2,5 GB)
```

## Três medições (cerca de 15 min no total; o agente fica parado só durante elas)
Antes de começar: deixe o agente terminar o trabalho em andamento e pare-o (`workers.acer_agent`). Os pedidos do
site esperam na fila e são atendidos quando ele voltar.

1. **Ao lado do ComfyUI carregado e parado** (a pergunta principal: cabe sem tirar o gerador da GPU?).
   O ComfyUI precisa estar ligado e já ter gerado uma imagem desde que abriu (modelo na memória), com a fila vazia.
   O script não gera nada no ComfyUI e não o descarrega.
   ```powershell
   & $py qwen_bench.py --modelo Qwen3-4B-Q4_K_M.gguf --com-comfyui-ocioso --saida resultados_qwen4b_com_comfyui.json
   ```
   Se não couber inteiro na GPU, o script para sozinho e grava o motivo (isso já é resposta).
2. **Sozinho na GPU** (mesma condição do teste do 8B). Feche o ComfyUI:
   ```powershell
   & $py qwen_bench.py --modelo Qwen3-4B-Q4_K_M.gguf --saida resultados_qwen4b.json
   ```
3. **Sob demanda** (carrega, planeja e descarrega a cada briefing; tempo total real de um uso por arte). ComfyUI fechado:
   ```powershell
   & $py qwen_bench.py --modelo Qwen3-4B-Q4_K_M.gguf --sob-demanda --saida resultados_qwen4b_sob_demanda.json
   ```
Depois: religar o ComfyUI e o agente do jeito normal e conferir com `& $py -m workers.acer_agent --check`.

Opcional (comparar o 8B no mesmo modo sob demanda):
`& $py qwen_bench.py --sob-demanda --saida resultados_qwen8b_sob_demanda.json`

## Devolver
Copiar os arquivos `resultados_qwen4b*.json` (e `resultados_qwen8b_sob_demanda.json`, se rodou) para
`C:\Users\windows\Downloads\sitegenius-rebuild\deploy\acer_qwen_bench\` na PE0.

## Rollback
Apagar `Qwen3-4B-Q4_K_M.gguf` (ou a pasta inteira `C:\IA-Imagens\qwen-bench`). Nada mais foi alterado.
