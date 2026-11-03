# Atualização incremental do Acer — v2.6 (02/11/2026)

**Para o ChatGPT executar no Acer Emanuelomelho.** A v2.5 continua valendo; só **um arquivo** muda.
Não mexer no ComfyUI, no workflow (Q3), no `workers\.env` nem no agente (`workers\acer_agent.py` não muda).

## Por que
O Acer compõe os textos das artes com a sua cópia de `studio\typography.py`. O estilo de texto "tecnologico" usava a
mesma fonte do "moderno" (Montserrat), então o pedido "muda a fonte pra algo mais tech" não mudava nada visível.
Agora ele usa Orbitron (já presente em `studio\fonts`). O resto da v2.6 roda no servidor/HostGator.

## 1. Backup
```powershell
cd C:\IA-Imagens\sitegenius
Copy-Item studio\typography.py studio\typography_v25.py
```

## 2. Copiar
De `sitegenius-rebuild\studio\typography.py` (PE0) para `C:\IA-Imagens\sitegenius\studio\typography.py`.

| Arquivo | SHA-256 (16 primeiros) |
|---|---|
| `studio\typography.py` | `a579e3a5e3883164` |

```powershell
Get-FileHash studio\typography.py -Algorithm SHA256 | ForEach-Object { $_.Hash.Substring(0,16).ToLower() }
```

## 3. Reiniciar o agente
Esperar a fila do ComfyUI vazia, encerrar o agente e iniciá-lo do jeito normal. `--check` deve continuar mostrando a
edição direta (v2.5) ativa.

## 4. Conferência no site
Numa arte com texto, pedir "muda a fonte para algo mais tecnológico": o título sai em Orbitron (letras largas e
geométricas).

## Rollback
```powershell
Copy-Item studio\typography_v25.py studio\typography.py -Force
```
Reiniciar o agente.
