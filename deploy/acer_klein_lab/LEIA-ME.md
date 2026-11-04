# Laboratório do FLUX.2 Klein no Acer — reaproveitando a fila de produção, isolado dela

## Como a fila de produção funciona (analisado em 04/11/2026)
- Fila: tabela `sg_jobs` no Supabase (schema `sitegenius_studio`). Fica no Supabase, não na HostGator.
- Servidor: o Studio no Vercel. Os workers fazem POST `/studio/worker/claim`, informam o andamento em
  `/jobs/<id>/progress` e entregam o resultado em `/jobs/<id>/complete`, sempre com assinatura HMAC por worker (`SG_WORKERS`).
- Imagens: Cloudinary (ou o próprio banco, quando não há Cloudinary). Cada envio tem limite de 4 MB por causa do Vercel.
- Agente do Acer (`workers/acer_agent.py`): pega trabalhos `render`, monta o workflow (`{{PROMPT}}`, `{{WIDTH}}`,
  `{{HEIGHT}}`, `{{SEED}}`), roda o ComfyUI e informa os tempos de cada etapa e a memória.

Compartilhar a MESMA fila não é seguro: o agente de produção pega qualquer trabalho de imagem (não há filtro por
ambiente nem por worker), e os testes entrariam nas cotas, no painel e no histórico reais. Separar exigiria mudar a
produção.

## O que foi feito (o mais simples e seguro, sem arquitetura paralela)
O MESMO código e o MESMO agente, numa cópia isolada:
- Banco: schema e papel `sitegenius_lab` no mesmo Supabase. O papel só enxerga o próprio schema (conferido: o acesso à
  fila de produção é negado). As tabelas foram criadas pelo mesmo código do servidor, e há uma conta de teste só do
  laboratório.
- Servidor: um deploy de TESTE do Vercel, que não é produção nem tem alias. As variáveis valem só para esse deploy:
  banco do laboratório, só a credencial `acerlab` e imagens no próprio banco. Os endereços de produção não mudaram.
- Agente: o mesmo `workers.acer_agent`, numa segunda janela, com variáveis de ambiente do laboratório (que têm
  prioridade sobre o `workers\.env`, que NÃO é alterado) e o workflow do Klein.
- PE0: `lab/lab_jobs.py enviar` põe os 20 trabalhos na fila do laboratório; `lab/lab_jobs.py coletar` baixa as imagens
  e as métricas.

## No Acer (uma sessão, cerca de 20 a 30 min)
1. Criar `C:\IA-Imagens\klein-lab\` e copiar para lá `preparar_workflow_klein.py` e `rodar_agente_lab.ps1`.
   Em `C:\IA-Imagens\klein-lab\segredos\` (com ACL restrita), copiar da PE0 `lab_url.txt` e `lab_worker_secret.txt`,
   que estão na pasta de segredos do laboratório na PE0 (nunca no Git).
2. Super-resolução: baixar `RealESRGAN_x4plus.pth` (BSD, 64 MB) de
   https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth para `models\upscale_models` do ComfyUI.
3. Exportar o workflow do Klein já testado em formato de API como `C:\IA-Imagens\klein-lab\klein_api.json` e converter:
   `& $py preparar_workflow_klein.py klein_api.json klein_modelo_agente.json`
4. Recomendado: pausar o agente de produção durante o lote (os pedidos do site esperam na fila de produção). Assim o
   ComfyUI não fica alternando entre o Klein e o Z-Image.
5. `powershell -ExecutionPolicy Bypass -File C:\IA-Imagens\klein-lab\rodar_agente_lab.ps1`. Quando a fila do
   laboratório esvaziar, fechar a janela e religar o agente de produção como sempre.
O ComfyUI de produção não é alterado: o workflow do Klein vai por trabalho, e os modelos ficam onde já estavam.

## LayerStyle (instância isolada, autorizada como opção A)
- `instalar_comfyui_lab.ps1`: cria `C:\IA-Imagens\comfyui-lab`, com venv próprio, PyTorch só de CPU e o nó
  ComfyUI_LayerStyle. Porta 8189. Não usa a GPU e não toca no ComfyUI de produção.
- `layerstyle_lab.py`: liga essa instância, monta as artes de `fila_ls\entrada`, devolve `fila_ls\saida` e desliga.
```powershell
powershell -ExecutionPolicy Bypass -File C:\IA-Imagens\klein-lab\instalar_comfyui_lab.ps1   # uma vez
& $py C:\IA-Imagens\klein-lab\layerstyle_lab.py --comfyui C:\IA-Imagens\comfyui-lab --fila C:\IA-Imagens\klein-lab\fila_ls
```
Copiar `fila_ls\` da PE0 antes e devolver `fila_ls\saida` depois.

## Limite conhecido
Pela fila atual, cada imagem volta com no máximo cerca de 1,45 MP, por causa do limite de 4 MB por envio no Vercel.
Isso atende feed, Story, banner e quadrado com super-resolução de verdade. Para 4K ou A4, a produção precisaria de
envio direto do Acer ao armazenamento (upload assinado ao Cloudinary). Nos testes, esses casos são completados na
PE0 e marcados como tal.

## Rollback
Fechar a janela do agente do laboratório e apagar `C:\IA-Imagens\klein-lab` (e `comfyui-lab`). Na PE0/Vercel:
apagar o deploy de teste e o schema `sitegenius_lab`. Nada de produção foi alterado.

## Rodada 2 (prompt adaptado ao Klein + 4K com envio direto) — 04/11/2026
O que mudou:
- **Endereço fixo do laboratório:** `https://sitegenius-studio-lab.vercel.app`. Atualize `segredos\lab_url.txt` com ele.
- **Envio direto:** imagens grandes (com lado maior que 2048 px ou mais de 3,5 MB) vão direto do Acer ao Cloudinary,
  na pasta `sitegenius-lab`. A autorização vale para um único arquivo daquele trabalho, e o servidor confere
  formato e dimensões antes de aceitar. O 4K de teste (3840×2160) já passou por esse caminho.
- **Tamanho final no workflow:** o workflow do Klein amplia até o tamanho final exato pedido.

No Acer:
1. Copiar da PE0 as pastas `workers\` e `studio\` do repositório para `C:\IA-Imagens\klein-lab\agente\`. É a cópia do
   laboratório; o agente de produção continua com o código dele.
2. Gerar o workflow de novo com o conversor atualizado:
   `& $py preparar_workflow_klein.py klein_api.json klein_modelo_agente.json`
   O padrão agora é ampliar até o tamanho final. Se o ComfyUI pedir `resolution_steps` em algum nó, mantenha o ajuste
   que já foi feito.
3. Atualizar `segredos\lab_url.txt` para `https://sitegenius-studio-lab.vercel.app`.
4. Pausar o agente de produção, rodar `rodar_agente_lab.ps1` até a fila esvaziar (5 trabalhos) e religar a produção.
