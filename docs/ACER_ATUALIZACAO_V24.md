# Atualização do Acer — v2.4: superfícies com OpenCV (02/11/2026)

**Para o ChatGPT executar no Acer Emanuelomelho.** Só arquivos Python do Studio mudam. Não precisa instalar
nada, não mexer no ComfyUI, no workflow (o Q3 continua), no `workers\.env` nem no agente (`workers\acer_agent.py`
não mudou).

## Por que atualizar
O Acer aplica os textos nas imagens (agente v2.3). As artes de produção de hoje ainda foram compostas com o código
antigo. Com esta atualização, no Acer:
- **Camiseta vestida e caneca:** segmentação com OpenCV (GrabCut). Separa a peça do fundo e, na camiseta, da pele.
  Na caneca, ignora a alça e a sombra. A estampa acompanha a curvatura e fica atrás do que estiver na frente
  (braço, dobra).
- **Placa inclinada ou vista de lado:** detecção de quadriláteros por contorno (OpenCV) + homografia. Se a placa
  estiver parcialmente coberta, o que está na frente continua na frente.
- Sem confiança na superfície, o comportamento seguro continua: texto em destaque e aviso ao cliente.
- Brilho (neon) só em letras claras.

Teste com as artes reais de hoje: a caneca, que antes falhava (texto flutuando acima dela), agora recebe o texto no
corpo, centralizado e longe da alça. Camiseta preta e placa: ok. A leitura visual (OCR) confirmou os três textos.

## 1. Fazer backup (rollback)
```powershell
cd C:\IA-Imagens\sitegenius
Copy-Item studio studio_backup_v23 -Recurse
```

## 2. Copiar os arquivos (de `sitegenius-rebuild` no PE0 para `C:\IA-Imagens\sitegenius`)
| Arquivo | SHA-256 (16 primeiros) |
|---|---|
| `studio\surfaces.py` | `a5f868c79274fe40` |
| `studio\typography.py` | `4cf017bb5198319e` |
| `studio\textplan.py` | `f2692028630775cc` |
| `studio\edits.py` | `c553cb65b15954e6` |
| `studio\creative.py` | `d9b2855801573390` |
| `studio\routes.py` | `2fcac867cedb507f` |

O agente só usa os três primeiros; os outros vão junto para manter a pasta `studio\` igual à do site.
Conferência do hash no Acer:
```powershell
Get-FileHash studio\surfaces.py -Algorithm SHA256 | ForEach-Object { $_.Hash.Substring(0,16).ToLower() }
```

## 3. Conferir o OpenCV (já veio com o RapidOCR; não instalar nada)
```powershell
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py -c "import cv2; print('opencv', cv2.__version__)"
& $py -c "from studio import surfaces, ocr; print('cv2 no Studio:', surfaces.cv2 is not None); print(ocr.status())"
```
Esperado: `cv2 no Studio: True` e `disponivel: True`.

## 4. Reiniciar o agente
Esperar a fila do ComfyUI ficar vazia, encerrar o agente e iniciá-lo do jeito normal (com o Q3, que já é o padrão).
Depois:
```powershell
& $py -m workers.acer_agent --check
```

## 5. Conferência
Pedir no Studio, por exemplo: "Uma caneca branca com Emanuel Tecnologia na lateral, foto de produto".
No admin, em "Atividade recente", deve aparecer "superfície: curva … OCR ok … no acer".

## Rollback
```powershell
Remove-Item studio -Recurse -Force; Rename-Item studio_backup_v23 studio
```
Depois, reiniciar o agente.
