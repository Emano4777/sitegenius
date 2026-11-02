# Otimização do Acer para pedidos diferentes (agente v2.2) — 02/11/2026

Documento técnico (interno). Nada daqui aparece para o cliente.

## Ponto de partida (medido pelo ChatGPT, 640×640, 9 passos)
| Cenário | Tempo |
|---|---|
| VAE na CPU, com cache | 65,6 s |
| VAE na GPU, 1ª geração (carga a frio) | 197,8 s |
| VAE na GPU, **mesmo prompt** (cache) | **19,4 s** |
| Decodificação VAE: GPU × CPU | 0,4 a 1,8 s × ~45 s |

## Diagnóstico
1. **Os 19,4 s são o caso ideal, não o real.** Com o prompt idêntico, o ComfyUI reaproveita a saída do
   `CLIPTextEncode`: o cache de nós usa as entradas como chave, então só reaproveita quando o texto é **o mesmo**.
   Isso também garante que o condicionamento de um prompt nunca é usado para outro. O agente não muda essa regra.
2. **Com um prompt novo**, o Qwen3-4B (codificador de texto) precisa rodar. Com `--lowvram` em 6 GB, a hipótese
   principal é a **troca de pesos entre RAM e VRAM**: o ComfyUI sobe o codificador para a VRAM, descarrega parte do
   Z-Image e, no KSampler, faz o caminho inverso. Isso custa mais do que a conta em si.
3. `--disable-pinned-memory` deixa cada transferência RAM ↔ VRAM mais lenta (memória não fixada).
4. O agente antigo ainda forçava recarga completa a cada tarefa (`/free` com `free_memory`), problema já corrigido na v2.1.

## O que o agente v2.2 mede (sem alterar o ComfyUI)
Por geração, enviados ao Studio e gravados no benchmark:
- `encode_s`: codificador de texto (inclui subir o codificador para a VRAM, se houver troca);
- **`preparo_amostragem_s`**: do início do KSampler até o 1º passo. Inclui recarregar o Z-Image na VRAM. **Se for alto
  (> 5 s), há troca de pesos.**
- `sampling_s`, `s_por_passo`, `vae_s`;
- `vram_min_encode_mb`, `vram_min_sampling_mb`, `vram_min_vae_mb`, `ram_min_mb`: mínimo de memória livre em cada etapa
  (amostrado a cada 0,5 s por `/system_stats`). Mostra quem ocupa a VRAM em cada etapa.

Opção nova, sem tocar no ComfyUI: **`SG_COMFY_CLIP_DEVICE=cpu`** (ou `--clip-device cpu` no benchmark). O agente põe
`device=cpu` no nó do codificador se o nó aceitar essa entrada (consulta `/object_info`). O codificador fica na RAM e
o Z-Image não sai da VRAM. A codificação fica mais lenta na CPU, mas some a troca. Só o benchmark diz se compensa.

## Benchmark com 5 prompts diferentes (cenário real)
Cada execução faz 1 aquecimento (descartado), 5 prompts **distintos** e, com `--repeat`, o último prompt de novo (como
em "outra versão"). Mesma resolução (640×640) e mesmos 9 passos do workflow: qualidade não é sacrificada.

```powershell
cd C:\IA-Imagens\sitegenius
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py -m pip install websocket-client          # necessário para as métricas por etapa
& $py -m workers.acer_agent --check
& $py -m workers.acer_agent --bench-prompts --size 640x640 --repeat --clip-device default,cpu --report
```
Saída: `workers\bench_prompts_resultado.json`, imagens `bench_prompt<N>_<codificador>_640x640.png` e linhas de log
`RESUMO codificador=... cenario=novo|repetido: comfy_s=... encode_s=... preparo_amostragem_s=... sampling_s=... vae_s=...`.

### Matriz sugerida (o ChatGPT altera as flags do ComfyUI entre as rodadas; o agente não altera nada)
| Rodada | Flags do ComfyUI | Objetivo |
|---|---|---|
| A | atuais (`--lowvram --disable-pinned-memory`, VAE na GPU) | linha de base com prompts diferentes |
| B | `--normalvram` (sem `--lowvram`) | ver se codificador + Z-Image Q2_K + VAE cabem juntos na VRAM e somem as trocas |
| C | B sem `--disable-pinned-memory` | transferências mais rápidas se ainda houver troca |
| D | B + `--reserve-vram 0.7` | margem contra OOM, mantendo o ganho de B |
| E | melhor de B a D + `--cache-lru 16` | reaproveitar pedidos idênticos recentes ("outra versão", vários usuários) |

Em cada rodada, rodar `--clip-device default,cpu`. **Critério de estabilidade:** nenhum erro de memória, `ram_min_mb` acima
de ~1500 e `vram_min_sampling_mb` acima de ~300. Com o vencedor escolhido, deixar `SG_COMFY_CLIP_DEVICE` no `workers\.env`.

### Como ler o resultado
- `preparo_amostragem_s` alto e `vram_min_encode_mb` baixo: há troca. Testar B/C e o codificador na CPU.
- Codificador na CPU com `comfy_s` menor no cenário "novo": adotar `SG_COMFY_CLIP_DEVICE=cpu`.
- "repetido" muito mais rápido que "novo": confirma o cache por pedido idêntico (correto e esperado).

## Otimizações compatíveis com a RTX 4050 6 GB (prioridade: qualidade e estabilidade)
1. Remover as trocas (rodadas B a D, ou o codificador na CPU): é o maior ganho esperado e não mexe na imagem.
2. VAE na GPU: já ativado (de ~45 s para ~1 s).
3. **Qualidade:** se a rodada B mostrar folga de VRAM, testar o Z-Image em Q3_K_M ou Q4_K_S no lugar do Q2_K. Fica um pouco
   mais pesado, mas melhora detalhes e texto. Comparar as imagens, não só o tempo.
4. `--fast` (acumulação fp16) pode dar um ganho pequeno. Testar e conferir se a imagem não muda.
5. Evitar atenção experimental (sage/flash) neste momento: pouco ganho em 640×640 e risco de instabilidade.
6. Não reduzir passos nem resolução para melhorar o benchmark (decisão do produto).

## Estimativas na tela do cliente
- O Studio mostra "Tempo estimado: cerca de N minutos" **só** com 3 medições reais ou mais do mesmo tamanho (gerações
  concluídas ou benchmark quente com prompt novo, enviado com `--report`). Sem isso, não mostra nada.
- Pedidos idênticos já em memória ("outra versão") não entram na estimativa de pedido novo, para ela não ficar otimista.
- Durante a geração, o tempo restante vem da velocidade real dos passos daquela imagem.

## Arquivos para sincronizar no Acer
| Arquivo | SHA-256 (16) |
|---|---|
| `workers\acer_agent.py` | `622603cfb87c62ee` |
| `studio\worker_client.py` | `f4026806e6341baa` |

Pode copiar a pasta `studio\` inteira de novo. Não mexer em `workers\.env` (exceto para adicionar `SG_COMFY_CLIP_DEVICE`
depois do benchmark) nem em `comfyui_workflow.json`. Dependência: `websocket-client`.

## Primeira medição real com o agente v2.2 (Preview, 02/11/2026 01:43, prompt novo, 1168×656, 9 passos)
| Etapa | Tempo |
|---|---|
| Codificador de texto (`encode_s`) | **136,5 s** (≈ 82% do total) |
| Preparo da amostragem (recarga do Z-Image) | 4,3 s |
| Amostragem (9 passos, 3,1 s/passo) | 28,0 s |
| VAE na GPU | 2,3 s |
| Total no ComfyUI / no agente | 166,9 s / 168,2 s |

Memória: VRAM mínima livre de 1528 MB (codificação), 1773 MB (amostragem) e 2814 MB (VAE); RAM mínima de 2732 MB; modelo em cache (sem recarga).

**Conclusão:** o gargalo com prompt novo é o **codificador de texto**, não a amostragem nem a troca do Z-Image (só 4,3 s).
136 s para codificar um prompt curto num modelo de 4B indica que o Qwen3-4B IQ2_XXS está rodando total ou parcialmente fora da GPU, ou com desquantização IQ2 muito cara.
Prioridades para o benchmark:
1. `--clip-device default,cpu` e `--normalvram` (rodadas A/B): ver se a codificação cai para poucos segundos.
2. Testar um GGUF do Qwen3-4B em **Q4_K_M ou Q8_0** (sem trocar o Z-Image): quantizações K e Q8 desquantizam muito mais rápido que IQ2_XXS na GPU e preservam melhor o entendimento do prompt (qualidade). O Qwen3-4B Q4_K_M ocupa cerca de 2,5 GB; com a VRAM livre observada, cabe com `--normalvram` se o Z-Image Q2_K ficar residente.
3. Manter 9 passos e a resolução: a amostragem já é rápida (28 s).
