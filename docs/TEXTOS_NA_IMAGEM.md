# Textos na imagem — pipeline (02/11/2026)

Documento técnico (interno). O modelo de imagem **nunca desenha letras**: ele faz a arte, e o Studio escreve
os textos reais por cima, com fontes de verdade, depois de conferir a grafia.

## Fluxo
| Etapa | Onde | O que faz |
|---|---|---|
| A. Interpretação | `studio/textplan.py` (+ intérprete da HostGator) | Extrai os textos do pedido **literalmente** (aspas, "título: …", "com o nome …", preços digitados), com papel (título, subtítulo, preço, preço antigo, botão, contato…), posição (topo, rodapé, centro, cantos, "abaixo do título"), estilo tipográfico, efeito e cor. Sugestões do modelo só entram se o texto existir literalmente no pedido. |
| B. Arte sem texto | `studio/creative.py` (`final_prompt`, `art_prompt_visual`) | Remove do prompt os textos, os preços e as menções a letras, logos, cartazes e "poster/anúncio". Reserva o espaço do texto ("the upper third of the frame is calm, open negative space…"). Acrescenta "no text…" e, se o pedido não fala de pessoas, "no people". |
| C. Tipografia | `studio/typography.py` | Amplia a arte para o tamanho de entrega (lado menor de 1080 px, lado maior até 1920 px). Mede cada grupo e acha o maior tamanho que cabe na zona, quebrando linhas só em espaços. Desliza o grupo para a faixa mais calma da arte e escolhe a cor pelo fundo. Aplica sombra, halo, véu ou painel conforme o contraste e os efeitos do estilo (brilho, metálico, contorno, degradê). Preço vira selo (R$ pequeno, centavos sobrescritos); botão vira pílula; o preço antigo aparece riscado. |
| D. Validação | `typography.validate` | Confere se os textos estão completos e idênticos ao pedido, se a fonte tem todos os caracteres, se nada saiu da área segura, se os grupos não se sobrepõem, o contraste WCAG medido nos pixels reais e o tamanho mínimo. Corrige sozinho quando dá (reduz, reforça o fundo) em até 6 tentativas. O relatório fica no `result` do job e aparece no admin (coluna "textos"). |

- A arte limpa fica guardada (`sg_assets.kind = 'art'`). **Ajustes de cena** usam essa arte como base, e não a versão
  com letras.
- **Ajustes só de texto** ("mude o título para …", "coloque no topo", "letras douradas") são refeitos na hora sobre a
  mesma arte, sem passar pelo gerador (`gen_type = 'text_edit'`, cerca de 1 s).
- Nenhuma fonte só de maiúsculas é usada em texto com minúsculas: a grafia nunca muda.
- Peça única com até 2 preços (anúncio, post, banner) é criação livre com o preço tipografado. Lista de produtos
  com preços continua no encarte (`compose.py`).

## Fontes (OFL, `studio/fonts`, licenças junto)
Poppins, Montserrat, Playfair Display, Cinzel, Orbitron, Anton, Bebas Neue, Archivo Black, DM Serif Display e Great Vibes.

## Limitações
- Texto **em superfícies e em perspectiva** (parede, camiseta, letreiro): o Studio aplica o texto em destaque, plano,
  sobre a arte. Não tenta deformar a escrita para a superfície, porque sem saber a geometria isso sairia errado.
- Sem OCR: a validação usa os dados do pedido e as medições de pixels. A detecção de "elementos importantes" é
  aproximada, pela densidade de bordas.
- Texto sem aspas depende dos padrões ("com o nome X", "título: X") ou da extração do modelo. Com aspas, é exato.

## Codificador Q3 (Qwen3-4B-Q3_K_S)
Medições do ChatGPT no Acer: arte com texto em 143,65 s (IQ2) contra 49,78 s (Q3); foto em 146,59 s contra 24,37 s;
codificação em 3 a 6 s contra mais de 120 s. Os testes reais de hoje ainda rodaram com o IQ2 (`encode_s` de cerca de 144 s).
Como agora a arte **não precisa** mais escrever letras, a precisão do codificador com textos deixou de ser crítica.
O que importa é entender a cena, e o Q3 (3 bits, quantização K) tende a ser **melhor** que o IQ2_XXS (2 bits).
Para ativar no Acer, só no arquivo do workflow do agente (o ComfyUI fica como está):
`workers\comfyui_workflow.json` → nó `CLIPLoaderGGUF` → `"clip_name": "Qwen3-4B-Q3_K_S.gguf"`.
Rollback: voltar para `Qwen3-4B-UD-IQ2_XXS.gguf`.

---

# Evolução v2 (02/11/2026): superfícies, leitura visual e textos sem aspas

## Textos sem aspas (`studio/textplan.py`)
- Nome da empresa: "minha empresa Emanuel Tecnologia", "minha loja X", "Emanuel Tecnologia escrito na parede".
- Rótulos sem dois-pontos: "título Super Ofertas, subtítulo Até 50% de desconto".
- Verbos: "coloca aceita financiamento" (condição comercial = subtítulo), "coloca Compre já no rodapé" (chamada = botão).
- Preços: "19,90", "R$ 19,90", "540 mil reais" (vira "R$ 540 mil", selo com "mil" na base); "preço em destaque".
- "meu nome no centro" posiciona a marca. O próprio nome ("…Tecnologia") não decide mais o estilo tipográfico.
- **Texto pedido sem conteúdo** ("uma frase de boas-vindas"): o Studio pergunta qual é e oferece uma sugestão
  ("Seja bem-vindo!"). Só usa a sugestão se o cliente responder "pode usar". Uma frase digitada é usada literalmente.
  O texto confirmado fica marcado (`confirmado`) e vale nos ajustes seguintes.
- Regra mantida: tudo o que é desenhado é literal do pedido (ou confirmado pelo cliente). O modelo só ajuda a classificar.

## Superfícies (`studio/surfaces.py`, numpy + Pillow)
| Superfície | Como | Resultado real |
|---|---|---|
| Placa, letreiro, quadro, outdoor, fachada | região lisa mais retangular -> quadrilátero (4 cantos) -> homografia (perspectiva) | Placa "Café Aroma": aplicada dentro da placa, OCR ok |
| Parede, muro | quadrilátero bem definido; senão o maior retângulo liso (frontal); letras com sombra oposta à luz | "Emanuel Tecnologia" na parede do escritório: relevo e sombra coerentes, OCR ok |
| Camiseta, caneca, garrafa | peça segmentada pela cor a partir do centro; texto em cilindro e arco, com as dobras (deslocamento pelo relevo da luz) | Funciona com fundo contrastante. Camiseta branca sobre fundo claro não separou -> a arte agora pede fundo contrastante |
- Integração: a luz e a sombra da arte multiplicam as letras, e a textura fina da superfície aparece por cima. O alfa é suavizado.
- Sem confiança: texto em destaque no topo (sem cobrir o objeto) e aviso natural ao cliente na conversa.
- A arte é gerada pedindo a superfície lisa, vazia e bem visível (`creative.SURFACE_HINTS`).

## Leitura visual (`studio/ocr.py`)
Motores avaliados:
| Motor | Peso | Resultado |
|---|---|---|
| **RapidOCR** (modelos PP-OCR do PaddleOCR em ONNX, CPU) | cerca de 110 a 130 MB de RAM, 0,7 a 3,4 s por imagem | **escolhido**: leu todos os textos reais |
| PaddleOCR completo | framework Paddle, centenas de MB | mesmos modelos, muito mais pesado |
| EasyOCR | PyTorch, mais de 1 GB | pesado demais para dividir o Acer com o ComfyUI |
| Tesseract | binário externo | não instalado; tende a errar em artes com efeitos |

- Comparação tolerante: ignora acentos e iguala confusões típicas (0/o, 1/l/i, 5/s, rn/m). Letras precisam bater
  exatamente depois disso; preços são conferidos pela sequência exata de algarismos.
- Detecta textos indesejados (letras na arte que ninguém pediu).
- Se um texto não é lido, reforça o contraste uma vez e lê de novo. O resultado fica no relatório (admin).
- Resultados: 7 peças corretas = 7 aprovadas (zero falso alarme); erro proposital "Emanel Tecnologia" = detectado.
- Onde roda: no Acer (agente v2.3, CPU), ver `docs/ACER_ATUALIZACAO_V23.md`. No servidor fica desligado
  (pacote ausente) e vale só a validação interna.

## Q3 × IQ2 (mesmos 7 pedidos, Z-Image Q2_K, 9 passos, mesmo VAE)
| | IQ2_XXS | Q3_K_S |
|---|---|---|
| Codificador | 132 a 144 s | **2,7 a 6,1 s** |
| ComfyUI por imagem | 166 a 177 s | **32 a 35 s** (1ª, a frio: 65 s) |
| VRAM mínima livre | 1,5 a 2,3 GB | 1,6 a 2,6 GB |
| RAM mínima livre | 1,6 a 3,2 GB | 1,9 a 2,5 GB (1ª, a frio: **198 MB**) |
| Falhas | 0 | 0 |
Qualidade equivalente ou melhor, com os espaços reservados ao texto respeitados com mais fidelidade.
**Recomendação: adotar o Q3**, com atenção à carga a frio (RAM): não abrir outros programas pesados no Acer
durante a 1ª geração, e manter `SG_OCR=0` como opção se a RAM apertar.

## Ajustes: o modelo interpreta, o servidor valida (`studio/edits.py`)
- O intérprete recebe os textos atuais da peça e devolve `tipo_pedido` (nova | ajuste) e `ajuste` (cena, textos,
  intensidade e operações: trocar, adicionar, remover, mover, aumentar, diminuir, cor, estilo, efeito).
- Ajuste só de texto: mesma arte, textos refeitos na hora. Ajuste de cena: img2img da arte limpa, com força
  0,55 (pequena), 0,65 (média) ou 0,8 (grande). Os textos não citados são mantidos.
- Validações: texto novo só se for literal na mensagem; o modelo marcar "cena" sem mudar a descrição da cena conta como
  ajuste só de texto; sinônimos de ação ("ajustar") são aceitos; o modelo dizer "nova" para uma mensagem que só fala da
  peça atual é corrigido (guarda); texto que já está no tamanho máximo gera uma resposta sincera ao cliente.
- Regras de frases (regex) ficam só como reserva, sem o modelo.
- A resposta bruta do modelo fica em `sg_jobs.result` do job de interpretação (diagnóstico do admin).
- Teste real (02/11/2026, gemma4:e4b): os 5 passos foram classificados corretamente ("o nome ficou meio pequeno" = só
  texto; "não curti o fundo… muda o slogan" = cena + texto; "e se as letras fossem douradas?" = só texto; "agora quero
  uma arte de pizzaria…" = nova). Interpretação de 30 a 42 s; ajuste só de texto pronto junto com a resposta.
