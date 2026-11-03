# Roda o MESMO agente de produção (workers.acer_agent, sem nenhuma alteração) apontado para a fila do LABORATÓRIO.
# As variáveis abaixo valem só nesta janela e têm prioridade sobre o workers\.env (que NÃO é alterado).
# Segredos: C:\IA-Imagens\klein-lab\segredos\ (lab_url.txt, lab_worker_secret.txt) — copiados da PE0, ACL restrita.
$ErrorActionPreference = "Stop"
$lab = "C:\IA-Imagens\klein-lab"
$env:SG_BASE_URL = (Get-Content "$lab\segredos\lab_url.txt" -Raw).Trim()
$env:SG_WORKER_ID = "acerlab"
$env:SG_WORKER_SECRET = (Get-Content "$lab\segredos\lab_worker_secret.txt" -Raw).Trim()
# Token de bypass dos deploys de teste do Vercel: o mesmo já usado no Acer. Se estiver no workers\.env, é lido de lá;
# senão, coloque-o em segredos\SITEGENIUS_BYPASS.txt.
if (Test-Path "$lab\segredos\SITEGENIUS_BYPASS.txt") { $env:SG_VERCEL_BYPASS = (Get-Content "$lab\segredos\SITEGENIUS_BYPASS.txt" -Raw).Trim() }
$env:SG_COMFY_WORKFLOW = "$lab\klein_modelo_agente.json"
Set-Location "C:\IA-Imagens\sitegenius"
& "C:\IA-Imagens\venv\Scripts\python.exe" -m workers.acer_agent --check
& "C:\IA-Imagens\venv\Scripts\python.exe" -m workers.acer_agent
