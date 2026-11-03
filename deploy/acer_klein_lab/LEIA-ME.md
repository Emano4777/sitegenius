# Executor isolado do FLUX.2 Klein no Acer

O executor recebe as solicitações da PE0 numa fila, executa o Klein no ComfyUI local, faz a super-resolução na GPU
do Acer quando o tamanho final pedido é maior que o que o Klein gera de uma vez, e devolve imagens e métricas
(tempo de geração e de ampliação, pico de VRAM, resolução). Ele funciona sozinho: não depende de nenhuma conversa aberta.

O que NÃO muda: agente do SiteGenius (`workers.acer_agent`), workflow e modelos de produção (Z-Image), Gemma.

## Convivência com a produção
- O executor só envia trabalho ao ComfyUI com a fila vazia e depois de 2 min sem nenhuma geração (`SG_LAB_OCIOSO_S`).
- Opcional: `--janela 00-07` limita a execução a um horário.
- Ao fim de cada lote, os modelos do Klein saem da memória. A geração de produção seguinte recarrega o Z-Image,
  o que custa cerca de 30 a 60 s uma vez.
- Se um pedido do site chegar durante uma geração do Klein, ele espera na fila do ComfyUI (cerca de 35 s).

## Instalação (uma vez)
1. Criar `C:\IA-Imagens\klein-lab` e copiar `klein_lab.py` para lá.
2. No ComfyUI, exportar o workflow do Klein que já foi testado (o de `C:\IA-Imagens\flux2-klein-lab`) em
   **formato de API** ("Save (API)") como `C:\IA-Imagens\klein-lab\klein_api.json`. O executor ajusta sozinho o
   texto, o tamanho, a semente e o nome do arquivo, identificando os nós pelo tipo.
3. Super-resolução: baixar o `RealESRGAN_x4plus.pth` (licença BSD, 64 MB) de
   https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
   para a pasta `models\upscale_models` do ComfyUI. Sem ele, o executor só gera e informa que a ampliação não está
   disponível.

## Fila
**Modo pasta** (já pronto): a PE0 coloca cada solicitação em `fila\entrada\<id>.json`. O executor grava
`fila\saida\<id>\imagem.png`, `ampliada.png` e `metricas.json`. Use uma pasta sincronizada entre as duas máquinas.
```powershell
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
cd C:\IA-Imagens\klein-lab
& $py klein_lab.py --workflow klein_api.json --fila pasta:C:\IA-Imagens\klein-lab\fila
```
**Modo fila no banco** (`--fila supabase`): uma tabela isolada, `sitegenius_lab.sg_lab_jobs`, com um papel de banco
que só enxerga essa tabela. É necessária a variável `SG_LAB_DATABASE_URL` no Acer. Só depois de autorizado
(script em `deploy/acer_klein_lab/fila_banco.sql`).

## Rollback
Parar o executor e apagar `C:\IA-Imagens\klein-lab` (e o `RealESRGAN_x4plus.pth`, se quiser). Nada mais foi alterado.

## LayerStyle numa instância isolada (autorizado: opção A)
- `instalar_comfyui_lab.ps1` cria `C:\IA-Imagens\comfyui-lab`, com ComfyUI, venv próprio, PyTorch só de CPU e o nó
  ComfyUI_LayerStyle. Essa instância não usa a GPU, não precisa de modelos e não toca no ComfyUI de produção.
- `layerstyle_lab.py` liga essa instância sozinho (porta 8189, `--cpu`), monta as artes da fila `fila_ls\entrada`
  com os nós do LayerStyle, devolve `fila_ls\saida\<id>\arte.png` e as métricas, e desliga a instância no fim. Junto vai
  o catálogo real dos nós (`object_info_layerstyle.json`), para o ajuste fino na PE0.
```powershell
powershell -ExecutionPolicy Bypass -File C:\IA-Imagens\klein-lab\instalar_comfyui_lab.ps1   # uma vez
& $py C:\IA-Imagens\klein-lab\layerstyle_lab.py --comfyui C:\IA-Imagens\comfyui-lab --fila C:\IA-Imagens\klein-lab\fila_ls
```

## Ordem sugerida para uma sessão completa
1. Copiar `deploy\acer_klein_lab\` (scripts, `fila\` e `fila_ls\`) da PE0 para `C:\IA-Imagens\klein-lab\`.
2. `klein_lab.py --uma-vez`: gera as 20 imagens do Klein (10 pedidos × abordagens A e B), já ampliadas.
3. `layerstyle_lab.py`: monta as artes do LayerStyle na fila.
4. Devolver para a PE0 as pastas `fila\saida` e `fila_ls\saida`, no mesmo caminho.
