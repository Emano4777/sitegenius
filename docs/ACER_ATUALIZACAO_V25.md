# Atualização do Acer — agente v2.5: imagens enviadas pelo cliente (02/11/2026)

**Para o ChatGPT executar no Acer Emanuelomelho.** Inclui tudo da V24 (superfícies com OpenCV). Não precisa instalar
nada novo (RapidOCR e OpenCV já estão lá). Não mexer no ComfyUI, no workflow (Q3) nem no `workers\.env`.

## O que já está no servidor/HostGator (NÃO depende do Acer — já em produção e testado)
| Pedido | Situação | Teste real |
|---|---|---|
| Pesquisa de fotos na internet ("pega da internet") | feito | busca real em Open Food Facts/Open Beauty Facts por EAN ou nome, com verificação de correspondência; nome genérico ("Leite") mostra opções em vez de escolher; não encontrado = aviso honesto. 1,5–3 s. Limite: pouca cobertura de itens de farmácia. |
| Chat e painel do encarte com o mesmo estado | feito | conversa PoupAqui real: 3 produtos e preços no painel, loja e Story registrados pelo chat, sem pedir de novo preço/validade. Causa corrigida: preço sem centavos ("45 reais", "é 55") era descartado; EAN só se o cliente digitar. |
| Demora em "Lendo seu pedido" | feito | métricas reais do Ollama em 10 pedidos: 31 s → ~16,5 s (resposta do modelo enxuta + consulta da fila a cada 2 s). Modelo já carregado (0 s), leitura do prompt 0,7 s. |
| Anexo nunca mistura com a arte anterior | feito | arte "Jefferson Tecnologia" + anexo imobiliário na mesma conversa: só o anexo é usado. |
| Redesign nunca redesenha produtos por IA | feito (servidor) | sem o Acer v2.5, o site pede os dados ao cliente; com o v2.5, lê a arte e monta o encarte com as fotos originais. |

## O que o agente passa a fazer
| Tarefa | Quando | Como |
|---|---|---|
| Edição direta (`reference_edit`) | "altera o valor do aluguel dessa imagem para 1000 reais" | lê a imagem do cliente (OCR), apaga o valor antigo (reconstrução do fundo), escreve o novo no mesmo lugar e entrega em 4:5 (Instagram). Fotos, logo e contatos ficam como estão. |
| Leitura para redesign (`reference_read`) | "moderniza essa arte mantendo produtos e preços" | lê produtos, preços e recorta as **fotos originais** da arte; o painel do encarte é preenchido para o cliente conferir; o encarte novo usa essas fotos (nada é redesenhado por IA). |

O agente só pega essas tarefas se a leitura visual e o OpenCV estiverem funcionando (capacidade anunciada ao servidor).
Sem a atualização, o site avisa o cliente com honestidade e nunca usa a arte errada.

## 1. Backup
```powershell
cd C:\IA-Imagens\sitegenius
Copy-Item studio studio_backup_v24 -Recurse
Copy-Item workers\acer_agent.py workers\acer_agent_v24.py
```

## 2. Copiar (de `sitegenius-rebuild` no PE0 para `C:\IA-Imagens\sitegenius`)
A pasta `studio\` inteira e `workers\acer_agent.py`. Conferência (16 primeiros caracteres do SHA-256):

| Arquivo | SHA-256 (16) |
|---|---|
| `workers\acer_agent.py` | `551de77d16b5db3b` |
| `studio\worker_client.py` | `8f841773185b7ba1` |
| `studio\refedit.py` | `a5ea1f21783f9655` |
| `studio\ocr.py` | `7ad81dd99d245ae7` |
| `studio\surfaces.py` | `a5f868c79274fe40` |
| `studio\typography.py` | `4cf017bb5198319e` |
| `studio\textplan.py` | `f2692028630775cc` |
| `studio\edits.py` | `c553cb65b15954e6` |
| `studio\creative.py` | `cde271046c54afed` |
| `studio\routes.py` | `e7a74cc2c129fd00` |
| `studio\products.py` | `c2c31f95ba487c36` |
| `studio\briefing.py` | `133f9ea485a1bde9` |

```powershell
Get-FileHash studio\refedit.py -Algorithm SHA256 | ForEach-Object { $_.Hash.Substring(0,16).ToLower() }
```

## 3. Conferir e reiniciar
```powershell
$py = "C:\IA-Imagens\venv\Scripts\python.exe"
& $py -m workers.acer_agent --check     # deve mostrar: "Edição direta de imagens enviadas (v2.5): ativa"
```
Esperar a fila do ComfyUI vazia, encerrar o agente e iniciá-lo do jeito normal. No log: `capacidades: reference_edit`.

## 4. Conferência no site
Anexar uma arte com preço e pedir "troca o preço dessa imagem para 49,90": a resposta deve dizer que vai trabalhar na
imagem enviada e o resultado sai em 1080x1350 com o preço trocado.

## Rollback
```powershell
Remove-Item studio -Recurse -Force; Rename-Item studio_backup_v24 studio
Copy-Item workers\acer_agent_v24.py workers\acer_agent.py -Force
```
Reiniciar o agente. O site volta a responder que não consegue editar dentro da imagem (sem misturar com outras artes).
