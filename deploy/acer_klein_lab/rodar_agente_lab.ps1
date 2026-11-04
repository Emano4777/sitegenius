# Agente do LABORATÓRIO: o mesmo programa do agente de produção, mas rodando de uma CÓPIA do código
# (C:\IA-Imagens\klein-lab\agente, com o envio direto ao Cloudinary e o tamanho final no workflow).
# O agente de produção (C:\IA-Imagens\sitegenius) e o workers\.env dele NÃO são alterados.
# Segredos: C:\IA-Imagens\klein-lab\segredos\ (lab_url.txt, lab_worker_secret.txt), ACL restrita.
$ErrorActionPreference = "Stop"
$lab = "C:\IA-Imagens\klein-lab"
$env:SG_BASE_URL = (Get-Content "$lab\segredos\lab_url.txt" -Raw).Trim()      # https://sitegenius-studio-lab.vercel.app
$env:SG_WORKER_ID = "acerlab"
$env:SG_WORKER_SECRET = (Get-Content "$lab\segredos\lab_worker_secret.txt" -Raw).Trim()
$env:SG_COMFY_WORKFLOW = "$lab\klein_modelo_agente.json"
# token de bypass dos deploys de teste do Vercel: o mesmo já usado pelo agente de produção (lido do .env dele, sem alterar)
$prodEnv = "C:\IA-Imagens\sitegenius\workers\.env"
if (Test-Path "$lab\segredos\SITEGENIUS_BYPASS.txt") {
    $env:SG_VERCEL_BYPASS = (Get-Content "$lab\segredos\SITEGENIUS_BYPASS.txt" -Raw).Trim()
} elseif (Test-Path $prodEnv) {
    $line = Select-String -Path $prodEnv -Pattern '^SG_VERCEL_BYPASS=' | Select-Object -First 1
    if ($line) { $env:SG_VERCEL_BYPASS = $line.Line.Split('=', 2)[1].Trim().Trim('"') }
}
Set-Location "$lab\agente"
& "C:\IA-Imagens\venv\Scripts\python.exe" -m workers.acer_agent --check
& "C:\IA-Imagens\venv\Scripts\python.exe" -m workers.acer_agent
