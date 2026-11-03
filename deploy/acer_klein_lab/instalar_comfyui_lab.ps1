# Instância ISOLADA do ComfyUI só para testar o ComfyUI_LayerStyle (autorizado em 03/11/2026, opção A).
# - Pasta própria (C:\IA-Imagens\comfyui-lab), venv próprio, porta 8189, SÓ CPU (--cpu): não usa a GPU nem a VRAM.
# - Não toca no ComfyUI de produção (porta 8188), no seu venv, nos seus nós nem nos modelos.
# - Não precisa de modelos de difusão: texto, contorno, sombra, brilho e mesclagem do LayerStyle rodam na CPU.
# Rollback: parar o processo e apagar C:\IA-Imagens\comfyui-lab.
$ErrorActionPreference = "Stop"
$base = "C:\IA-Imagens\comfyui-lab"
$py = "C:\IA-Imagens\venv\Scripts\python.exe"   # só para criar o venv novo (nada é instalado nele)
if (-not (Test-Path $base)) { git clone --depth 1 https://github.com/comfyanonymous/ComfyUI $base }
Set-Location $base
if (-not (Test-Path "$base\venv\Scripts\python.exe")) { & $py -m venv "$base\venv" }
$vpy = "$base\venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
& $vpy -m pip install -r requirements.txt
if (-not (Test-Path "$base\custom_nodes\ComfyUI_LayerStyle")) {
    git clone --depth 1 https://github.com/chflame163/ComfyUI_LayerStyle "$base\custom_nodes\ComfyUI_LayerStyle"
}
& $vpy -m pip install -r "$base\custom_nodes\ComfyUI_LayerStyle\requirements.txt"
Write-Host "Instalado. Para iniciar (deixe numa janela própria):"
Write-Host "  & $vpy $base\main.py --listen 127.0.0.1 --port 8189 --cpu --disable-auto-launch"
