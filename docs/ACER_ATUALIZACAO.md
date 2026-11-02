# Atualização do agente no Acer Nitro (Emanuelomelho) — criação livre (v2)

Origem: PE0, `C:\Users\windows\Downloads\sitegenius-rebuild` (branch `feature/sitegenius-ai-studio`).
Destino: `C:\IA-Imagens\sitegenius`. Python: `C:\IA-Imagens\venv\Scripts\python.exe`.

## 1. Arquivos a copiar (substituir)

| Arquivo | Mudou? | SHA-256 (16 primeiros) |
|---|---|---|
| `workers\acer_agent.py` | **sim** (free_image, img2img, seed, tempos, `--bench`) | `b2357efb1f84bea0` |
| `studio\worker_client.py` | **sim** (`progress` envia tempos) | `4c6a7bb92b689921` |
| `studio\compose.py` | não (copiar para garantir a mesma versão) | `8e1a4371ef40dbfe` |
| `studio\__init__.py` | sim (importado pelo pacote) | `e5101f0442d1783d` |
| `studio\config.py` | sim (importado pelo pacote) | `58a6644a29bdeba6` |
| `studio\db.py` | sim (importado pelo pacote) | `2bb400bbc4a8e9cb` |
| `scripts\teste_casos_reais.py` | novo (opcional: testes pela API) | `47afe5caa8d36e97` |

Mais simples: copiar a pasta `studio\` inteira e `workers\acer_agent.py`.
**Não copiar** e **não alterar** `workers\.env` (já tem o segredo e o `SG_VERCEL_BYPASS`) nem
`comfyui_workflow.json` (workflow real já validado).

Conferir o hash no Acer (PowerShell):
```powershell
cd C:\IA-Imagens\sitegenius
(Get-FileHash workers\acer_agent.py -Algorithm SHA256).Hash.Substring(0,16).ToLower()
```

## 2. Dependências
Nenhuma nova: `requests`, `Pillow` e `flask` (já instaladas). O ComfyUI precisa dos nós padrão
`LoadImage`, `ImageScale` e `VAEEncode` (vêm com o ComfyUI) para o img2img.

## 3. Testes, nesta ordem (ComfyUI ligado em 127.0.0.1:8188)

```powershell
cd C:\IA-Imagens\sitegenius
$py = "C:\IA-Imagens\venv\Scripts\python.exe"

# 3.1 Configuração, ComfyUI e transformação img2img do workflow real (não gera nada)
& $py -m workers.acer_agent --check

# 3.2 Benchmark REAL de tempo e memória (sem servidor). A 1ª execução inclui carregar o modelo.
#     Gera bench_<tamanho>_<n>.png e workers\bench_resultado.json
& $py -m workers.acer_agent --bench 640x640,768x960,656x1168 --runs 2

# 3.3 Agente em loop (atende o Preview)
& $py -m workers.acer_agent
```

Com o agente em loop, abrir https://sitegenius-studio-preview.vercel.app/studio/ (logado no Vercel), criar uma
conta em `/studio/entrar` e pedir, por exemplo:
- (a) `astronauta em floresta cinematográfica`
- (b) anexar a foto de uma sala e pedir `anúncio imobiliário desta sala, mais elegante ao entardecer`
- (c) `Encarte de hortifruti, formato quadrado` + `Tomate kg 6,99` e clicar em **Gerar encarte**

Ou, em outro terminal, rodar os três casos automaticamente (baixa as imagens e grava os tempos):
```powershell
$env:SG_VERCEL_BYPASS = (Get-Content workers\.env | Select-String '^SG_VERCEL_BYPASS=').Line.Split('=',2)[1]
$env:SG_TEST_EMAIL = "teste-acer@exemplo.com"; $env:SG_TEST_SENHA = "<senha com 10+ caracteres>"
& $py scripts\teste_casos_reais.py --ref C:\caminho\foto_sala.jpg --saida C:\IA-Imagens\resultados_teste_real
```

## 4. O que devolver para a PE0
- A saída do `--check` e o `workers\bench_resultado.json`.
- As imagens `bench_*.png` e `resultados_teste_real\caso_*.jpg`, mais o `resultado.json`.
- As últimas linhas do log do agente (cada tarefa termina com uma linha `concluída em Xs {...tempos...}`).

## 5. O que mudou no comportamento
- **free_image**: a imagem do ComfyUI é entregue direto, sem cards, preços ou logo.
- **img2img real**: a referência (ou a imagem anterior, quando o pedido é um ajuste) vai para `/upload/image` e
  entra no workflow por `LoadImage → ImageScale → VAEEncode`, com `denoise` de 0,5 a 0,85 (edição: 0,6).
- **Seed** vem do servidor: "Outra versão" usa uma seed nova; ajustes partem da imagem anterior.
- **Tempos** (fila do ComfyUI, execução, download, upload, composição, VRAM/RAM livres) vão para o servidor, que
  calcula as estimativas da tela com base nessas medições.
- O agente guarda a mediana local em `workers\.timings.json` para a barra de progresso ("faltam ~Xs pela média medida").
- Encartes (ad_composite) continuam iguais.
- Observação: o Z-Image Turbo roda com `cfg=1`, então o **negative prompt não tem efeito** nesse modelo. Ele fica
  registrado no job, mas não muda a imagem.

---

# Atualização de desempenho — 02/11/2026

## Arquivos (substituir no Acer)
| Arquivo | SHA-256 (16) | O que mudou |
|---|---|---|
| `workers\acer_agent.py` | `96faff02414318fc` | sem recarga do modelo entre tarefas; progresso por WebSocket; passos do perfil; `--bench --steps --report` |
| `studio\worker_client.py` | `449d4b35ff430028` | `report_benchmarks()` e tempos no `progress()` |

Se preferir, copie a pasta `studio\` inteira de novo. **Não** mexer em `workers\.env` nem em `comfyui_workflow.json`.

## Dependência nova (opcional, recomendada)
```powershell
C:\IA-Imagens\venv\Scripts\python.exe -m pip install websocket-client
```
Sem ela tudo funciona, mas o progresso volta a ser só "Gerando (Xs)" e não há tempos por etapa.

## Mudança principal: o modelo não é mais recarregado a cada imagem
O agente antigo chamava `POST /free {"free_memory": true}` depois de cada tarefa. No ComfyUI isso executa
`executor.reset()`, que limpa o cache dos nós, e a tarefa seguinte **reexecuta UnetLoaderGGUF, CLIPLoaderGGUF e
VAELoader** (recarga completa). Agora o modelo fica carregado, e só há liberação se a RAM livre ficar abaixo de
`SG_COMFY_MIN_FREE_RAM_MB` (padrão 1200). O comportamento antigo pode ser restaurado com `SG_COMFY_FREE_AFTER_JOB=1`.
Com o WebSocket, cada tarefa registra `modelo_em_cache` (1 = sem recarga) e `load_s`.

## Comandos de teste
```powershell
cd C:\IA-Imagens\sitegenius
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py -m workers.acer_agent --check     # agora também confere a troca de passos e o websocket-client

# Benchmark controlado: a 1ª execução é fria (carga do modelo); as seguintes são quentes (modelo em cache).
# Repetir para cada configuração do ComfyUI testada (lowvram x normalvram, cpu-vae x VAE na GPU).
& $py -m workers.acer_agent --bench 640x640,768x960 --steps 6,9 --runs 3 --report
#   --report envia o resultado ao Studio: a tela passa a mostrar "~X (benchmark)" para esses tamanhos.
#   --free-between força a recarga entre execuções (só para medir o custo da carga a frio).
```
Cada linha do `bench_resultado.json` traz `comfy_s`, `load_s`, `encode_s`, `sampling_s`, `vae_s`, `s_por_passo`,
`modelo_em_cache`, RAM e VRAM livres antes, e a imagem `bench_<tamanho>_<passos>p_<n>.png`.

## Depois do benchmark: escolher os perfis (no Vercel, sem mexer em código)
Com os tempos e a qualidade visual comparados, definir no Vercel (Preview) a variável:
```
SG_RENDER_PROFILES={"rapido": {"megapixels": 0.41, "steps": 6}, "qualidade": {"megapixels": 0.78, "steps": 9}}
```
- `megapixels` de 0,15 a 1,2 (o tamanho de cada formato é recalculado em múltiplos de 16);
- `steps` de 1 a 30 (vale para todos os KSampler; no img2img o número efetivo é `steps × denoise`).
Sem a variável valem os tamanhos atuais (Qualidade ≈0,78 MP, Rápido ≈0,41 MP) e os passos do workflow.
