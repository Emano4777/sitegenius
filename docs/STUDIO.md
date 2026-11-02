# SiteGenius Studio — estúdio de encartes com IA

Branch: `feature/sitegenius-ai-studio`. Nada foi publicado em produção.

## Arquitetura

```
 Navegador ──HTTPS──▶ Vercel (Flask app.py + blueprint /studio)
                          │  Postgres: tabelas sg_* (conversas, mensagens, assets, jobs, rate, nonces)
                          ▲
        polling HTTPS assinado (HMAC, só saída)    polling HTTPS assinado (HMAC, só saída)
                          │                                   │
   HostGator: hostgator_interpreter.py          Acer Nitro: acer_agent.py
   └─ Ollama 127.0.0.1:11434 (gemma4:e4b)       └─ ComfyUI 127.0.0.1:8188 (Z-Image Turbo)
```

- **Nenhuma porta nova é aberta.** HostGator e Acer *buscam* tarefas no SiteGenius.
- **A IA não inventa ofertas.** O modelo de linguagem só propõe o briefing. O servidor descarta
  qualquer preço que o usuário não tenha digitado (`normalize_briefing`). O modelo de imagem só
  gera o **cenário**; fotos, nomes, preços, validade e logo são desenhados por `studio/compose.py`
  (Pillow, determinístico).
- **Sem IA configurada, o Studio funciona** em “modo regras” (parser em `studio/briefing.py`).
  Se o worker do HostGator falhar ou demorar mais que `SG_INTERPRET_TIMEOUT_S`, cai para as regras.
- **Visão:** `/api/tags` do HostGator informa `vision` em `gemma4:e4b`, `gemma4:e2b` e `qwen3.5:4b`.
  O interpretador consulta `/api/show` ao iniciar e, se o modelo não tiver visão, ignora as imagens
  e registra isso no log, sem fingir análise. **Qualidade e latência da visão ainda não foram
  testadas** (ver pendências).

## Arquivos

| Caminho | Função |
|---|---|
| `studio/routes.py` | Rotas `/studio` (usuário) e `/studio/worker` (HMAC) |
| `studio/briefing.py` | Parser por regras, validação, bloqueio de preço inventado, prompt do cenário |
| `studio/compose.py` | Composição determinística do encarte |
| `studio/security.py` | HMAC + anti-replay, rate limit no banco, sanitização de imagem, anti-SSRF |
| `studio/products.py` | Busca por EAN/nome no Open Food Facts / Open Beauty Facts (CC BY-SA) |
| `studio/db.py`, `studio/migrate.py` | Postgres/SQLite, tabelas `sg_*` |
| `workers/acer_agent.py` | Agente do Acer (ComfyUI + composição) |
| `workers/hostgator_interpreter.py` | Interpretador no HostGator (stdlib, Ollama local) |
| `scripts/dev_studio.py` | Servidor local só do Studio (não importa `app.py`) |

## Variáveis de ambiente (Vercel)

| Variável | Obrigatória | Descrição |
|---|---|---|
| `FLASK_SECRET_KEY` | **sim** | Sem ela o `/studio` fica desligado (a chave antiga está pública no GitHub) |
| `SG_STUDIO_DATABASE_URL` | **sim** | Postgres do Studio. O ideal é um usuário próprio com permissão só nas tabelas `sg_*` |
| `SG_WORKERS` | para gerar | `acer-nitro:render:<segredo>,hostgator-interp:interpret:<segredo>` (segredo com 32 caracteres ou mais) |
| `SG_INTERPRETER_MODE` | não | `rules` (padrão) ou `queue` (usa o worker HostGator) |
| `SG_INTERPRET_TIMEOUT_S` | não | padrão 120 |
| `SG_DAILY_JOB_QUOTA` | não | gerações por usuário por dia (padrão 20) |

Limites fixos: upload de 4 MB (o corpo de requisição do Vercel aceita no máximo 4,5 MB); só JPG, PNG
e WEBP, validados pelo conteúdo e recodificados (remove EXIF); 30 mensagens por minuto, 30 uploads
a cada 10 minutos, 6 gerações a cada 10 minutos e 2 gerações simultâneas por usuário.

## Como testar localmente

```powershell
cd C:\Users\windows\Downloads\sitegenius-rebuild
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.\.venv\Scripts\python.exe -m pytest -q                       # 65 testes

# preview (login de dev automático, SQLite em instance/)
$env:SG_WORKERS = "acer-dev:render:<segredo do workers\.env>"
.\.venv\Scripts\python.exe scripts\dev_studio.py              # http://127.0.0.1:5055/studio/
# em outro terminal: processa gerações sem GPU (fundo degradê)
.\.venv\Scripts\python.exe -m workers.acer_agent --no-comfy
```

## Deploy (só depois da validação) e rollback

1. **Antes de tudo**, faça a rotação dos segredos expostos (ver seção de segurança).
2. Verifique a colisão de rota: `SELECT subdomain FROM user_templates WHERE lower(subdomain) = 'studio';`
   precisa retornar zero linhas, porque `/studio` passa a ser do Studio.
3. Mostre o SQL com `python -m studio.migrate` e aplique com `python -m studio.migrate --apply`.
   Só cria tabelas `sg_*` com `IF NOT EXISTS`.
4. Configure as variáveis no Vercel em **Preview**, faça o deploy de preview da branch e valide.
5. Produção somente com a sua aprovação.
6. **Rollback:** promova o deploy anterior no Vercel (instantâneo). As tabelas `sg_*` são
   independentes; para removê-las, rode `DROP TABLE sg_conversations, sg_messages, sg_assets,
   sg_jobs, sg_rate, sg_nonces;`. Mudanças em `app.py` (5 linhas): `secret_key` lida do ambiente
   (com o mesmo valor antigo como fallback), CORS sem `/studio` e `init_studio(app)`.

## Integração Acer Nitro (Emanuelomelho)

1. Copie a pasta do projeto (ou só `studio/` + `workers/`) para `C:\IA-Imagens\sitegenius`.
2. Instale as dependências: `pip install requests Pillow flask`.
3. No ComfyUI, abra o workflow já testado (`teste_zimage_q2_hd.py`) e use **Save (API Format)**.
   Troque o texto do prompt por `{{PROMPT}}`, a seed por `"{{SEED}}"` e largura/altura por
   `"{{WIDTH}}"`/`"{{HEIGHT}}"`. Existe um modelo em `workers/comfyui_workflow.example.json`,
   mas os nomes de nós e arquivos dele **precisam ser conferidos**.
4. Crie `workers\.env` a partir de `.env.example`, rode `python -m workers.acer_agent --check`
   e depois `--once`.
5. Para rodar continuamente, use o Agendador de Tarefas (“ao fazer logon”) com o comando
   `python -m workers.acer_agent`.
   Resoluções do cenário: até cerca de 786 mil pixels (por exemplo 768x960), na faixa do teste de
   768x1024 (cerca de 295 s). O agente chama `/free` após cada geração por causa dos 16 GB de RAM.

## Integração HostGator (WhatsApp intocado)

Estado verificado em 30/10/2026, só com leitura: `ollama-piloto.service` ativo, Ollama 0.40.12
em `127.0.0.1:11434`, modelos `gemma4:e4b`, `gemma4:e2b`, `qwen3.5:4b-q4_K_M` e `qwen3:4b-instruct`.

Plano (cada passo exige sua autorização):
1. Leitura apenas: descobrir qual modelo, `keep_alive` e `num_ctx` o worker do WhatsApp usa.
   Se o Studio usar outro `num_ctx` ou `keep_alive`, o Ollama recarrega o modelo ou muda o tempo
   de descarte, e isso afetaria o WhatsApp. O interpretador não inicia sem esses valores.
2. Criar um usuário Linux próprio (`sitegenius`) e copiar só `hostgator_interpreter.py`
   (biblioteca padrão, sem dependências).
3. Criar uma unit systemd **separada**, `sitegenius-interpreter.service`, com `Nice=10`,
   `MemoryMax=256M`, `CPUQuota=50%` e `EnvironmentFile=/etc/sitegenius/interpreter.env` (chmod 600).
   Não mexer em nenhuma unit existente.
4. Rodar `--check` (não infere) e depois `--once` com uma mensagem de teste.
5. Ativar `SG_INTERPRETER_MODE=queue` no Vercel.

Risco a medir: o Ollama processa as requisições em fila e uma interpretação do Studio pode atrasar
uma resposta do WhatsApp. É preciso medir latência e CPU antes de ativar.

## Segurança: problemas encontrados no projeto existente (não corrigidos nesta branch)

O repositório `Emano4777/sitegenius` é **PÚBLICO** e contém:
- senha do Postgres de produção em `app.py` (`get_db_connection`), com a porta 5432 publicada;
- `api_secret` da Cloudinary;
- access token de produção do Mercado Pago;
- `certificado.pem` com **chave privada** (EFI/Pix);
- `secret_key` fixa (`minha-chave-segura`), que permite forjar sessões de qualquer usuário, inclusive admin;
- arquivos de sessão em `flask_session/`.

Além disso: `login_required_admin` só verifica se há login (qualquer usuário é admin) e o CORS
devolve qualquer origem com credenciais.

**Ação recomendada:** trocar todas essas credenciais, movê-las para variáveis de ambiente,
restringir a porta 5432 e avaliar tornar o repositório privado. Apagar do código não basta,
porque os valores continuam no histórico do Git.

## Pendências

- [ ] Rotação de segredos e `FLASK_SECRET_KEY` (bloqueia o Studio em produção)
- [ ] Banco do Studio: definir `SG_STUDIO_DATABASE_URL` (de preferência com usuário restrito) e rodar a migração
- [ ] Verificar colisão `subdomain = 'studio'`
- [ ] Acer: exportar o workflow real, rodar `--check`/`--once` e medir tempo e RAM
- [ ] HostGator: ler a configuração do worker do WhatsApp, instalar a unit separada e medir impacto
- [ ] Testar qualidade e latência de visão do `gemma4:e4b` com imagens reais
- [ ] Deploy de preview no Vercel e validação visual
