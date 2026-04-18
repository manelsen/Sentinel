# Sentinel

Base executavel da V1 do Sentinel descrito no PRD: ingestao de mensagens, transcricao via Groq, classificacao via Gemini, persistencia em SQLite, heuristicas locais, alertas auditaveis e relatorio diario.

## Escopo desta base

- SQLite com schema auditavel e FTS5.
- Pipeline local para mensagens de texto e audio.
- API HTTP simples para sidecars externos.
- Sidecars reais para WhatsApp com bridge HTTP para o Sentinel.
- Transcricao nativa via Groq Speech-to-Text.
- Classificacao estruturada via Gemini JSON Schema.
- Normalizacao de texto preservando sinais de agressividade.
- Features por mensagem e por janela.
- Janelas hibridas curtas e expandidas.
- Fallback heuristico quando provider falha ou nao esta configurado.
- Emissao de alerta em `stdout`.
- Relatorio diario consolidado.
- Registro de feedback do moderador.

## Limites atuais

- O sidecar WhatsApp faz apenas ingestao. Envio de mensagens de moderacao para WhatsApp ainda nao foi implementado.
- Nao ha dashboard web; a saida continua estruturada em SQLite, JSON e `stdout`.
- O fallback heuristico continua sendo usado quando Groq/Gemini nao estiverem configurados ou responderem com erro.

## Uso rapido

Fluxo recomendado (padrao): `.env` + `docker compose`.

1. Copie o arquivo de ambiente:

```bash
cp .env.example .env
```

2. Ajuste as chaves no `.env`:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `SENTINEL_AUTH_TOKEN` (opcional)

O `docker compose` le `.env` automaticamente na raiz do projeto.

3. Suba Sentinel + sidecar:

```bash
docker compose up --build -d
```

4. Verifique saude:

```bash
curl -s http://127.0.0.1:8080/healthz
```

Fluxo manual (sem docker compose):

Inicializar o banco:

```bash
python3 -m sentinel.cli init-db --db sentinel.db
```

Subir a API HTTP para o sidecar:

```bash
PYTHONPATH=src python3 -m sentinel.cli --config sentinel.toml serve
```

Ingerir um evento JSON localmente:

```bash
PYTHONPATH=src python3 -m sentinel.cli ingest --db sentinel.db --event-file examples/hostile-message.json
```

Gerar relatorio diario:

```bash
PYTHONPATH=src python3 -m sentinel.cli report-daily --db sentinel.db --group-id grp_demo --date 2026-04-18
```

Registrar feedback:

```bash
PYTHONPATH=src python3 -m sentinel.cli feedback --db sentinel.db --incident-id inc_123 --feedback-type correto --note "Alerta util"
```

## Evento JSON esperado

```json
{
  "platform": "demo",
  "external_group_id": "grupo-1",
  "group_name": "Grupo 1",
  "external_user_id": "alice",
  "user_name": "Alice",
  "message_type": "text",
  "raw_text": "Voce esta distorcendo tudo de novo.",
  "sent_at": "2026-04-18T16:00:10Z",
  "received_at": "2026-04-18T16:00:11Z",
  "reply_to_message_id": null
}
```

Para audios, informe `message_type = "audio"` e `media_path` apontando para um arquivo local. Se `transcript_text` vier preenchido, o Sentinel usa esse texto e nao chama Groq.

## Configuracao

Copie `sentinel.toml` e ajuste pesos, thresholds, host/porta da API e providers.

Variaveis de ambiente esperadas (preferencialmente em `.env`):

```bash
cp .env.example .env
# edite o arquivo .env com suas credenciais e parametros
```

O CLI carrega `.env` automaticamente por padrao. Para desativar, use `--no-env-file`.

Overrides por ambiente aceitos no Sentinel:

- `SENTINEL_DB_PATH`
- `SENTINEL_SERVER_HOST`
- `SENTINEL_SERVER_PORT`
- `SENTINEL_AUTH_TOKEN`

## Sidecar vs bridge

No contexto do Sentinel:

- `sidecar` e o processo auxiliar que roda ao lado do pipeline principal e fala com o sistema externo.
- `bridge` e a funcao que esse processo exerce: traduzir eventos externos para o contrato HTTP `POST /ingest`.

Em outras palavras: o `whatsmeow` e a implementacao de sidecar; a serializacao do evento para o payload JSON do Sentinel e a bridge.

## Qualidade

O projeto agora usa:

- `Pydantic` nas bordas de ingestao, API HTTP e schema de classificacao;
- `pyright` para validacao estatica de tipos;
- `ruff` para lint e organizacao de imports.

Comandos:

```bash
uv run python -m unittest discover -s tests
uv run --extra dev pyright
uv run --extra dev ruff check .
uv run --extra dev ruff format .
```

Fixtures offline de providers:

```bash
GROQ_API_KEY=... GEMINI_API_KEY=... uv run python scripts/refresh_provider_fixtures.py
```

O script tenta atualizar as fixtures reais de Groq e Gemini. Se um provider falhar, ele grava a fixture de erro correspondente sem quebrar a fixture offline ja existente do caminho feliz.

## Sidecar WhatsMeow

O sidecar `whatsmeow` e a opcao recomendada para operacao mais previsivel em container, mantendo a mesma bridge HTTP do Sentinel.

Instalacao:

```bash
cd sidecar/whatsmeow
go mod tidy
```

Execucao:

```bash
export SENTINEL_INGEST_URL=http://127.0.0.1:8080/ingest
export SENTINEL_AUTH_TOKEN=
export SENTINEL_WHATSMEOW_STORE=./.store/whatsmeow.db
export SENTINEL_MEDIA_DIR=./.media
export SENTINEL_GROUPS_ONLY=true
export SENTINEL_IGNORE_FROM_ME=true
go run .
```

Build local:

```bash
go build .
```

O sidecar:

- conecta ao WhatsApp via `go.mau.fi/whatsmeow`;
- persiste a sessao em SQLite local;
- imprime QR no terminal quando precisar parear;
- consome eventos `*events.Message`;
- baixa audio localmente quando houver `AudioMessage`;
- serializa o evento no mesmo payload JSON do Sentinel;
- envia para `POST /ingest`.

## Endpoints HTTP

- `GET /healthz`
- `POST /ingest`
- `POST /feedback`
- `POST /report-daily`

Se `server.auth_token` estiver configurado, envie `Authorization: Bearer <token>`.

## Arquitetura detalhada

O Sentinel V1 e composto por quatro blocos principais:

1. **Entrada (CLI, HTTP e sidecars)**: recebe eventos normalizados no contrato `IncomingMessage`.
2. **Pipeline de analise**: persiste mensagem, normaliza texto, extrai features, monta janelas e classifica risco.
3. **Persistencia auditavel (SQLite + FTS5)**: registra tudo com trilha para revisao humana.
4. **Saida operacional**: alertas em canais configurados e relatorios diarios.

### Fluxo ponta a ponta

1. Sidecar ou cliente envia evento para `POST /ingest` (ou CLI `ingest`).
2. `SentinelService.ingest_message` faz upsert de grupo/usuario e grava mensagem bruta.
3. Se for audio:
   - usa `transcript_text` fornecido no evento, ou
   - tenta transcrever com Groq (`GroqTranscriber`), ou
   - registra falha quando provider nao esta configurado (`NoopTranscriber`).
4. Texto de analise (`raw_text` ou transcricao) passa por normalizacao (`normalize_text`).
5. Pipeline grava `normalized_messages`, indexa em `message_search` (FTS5) e calcula `message_features`.
6. Pipeline monta janela curta hibrida (tempo + max mensagens) e calcula `window_features`.
7. Se risco da janela curta cruzar threshold configurado:
   - abre janela expandida,
   - gera prompt estruturado,
   - classifica com Gemini JSON Schema ou fallback heuristico.
8. Resultado vira `incident_assessments`; se severidade e cooldown permitirem, cria `alerts`.
9. Fluxo retorna `IngestResult` com `severity`, `risk_score`, ids de avaliacao e alertas.

## Estrutura do repositorio

```text
.
├── src/sentinel/
│   ├── cli.py              # comandos CLI
│   ├── server.py           # API HTTP
│   ├── service.py          # orquestracao principal do pipeline
│   ├── schema.py           # schema SQL completo
│   ├── db.py               # conexao/init SQLite
│   ├── models.py           # modelos Pydantic e tipos de saida
│   ├── normalization.py    # normalizacao e sinais lexicais
│   ├── heuristics.py       # features por msg e por janela
│   ├── prompts.py          # prompt de classificacao
│   ├── classifier.py       # Gemini/command/fallback
│   ├── providers.py        # Groq e Gemini clients
│   ├── alerts.py           # payload e render de alertas
│   ├── reports.py          # consolidacao relatorio diario
│   └── utils.py            # tempo e IDs
├── tests/                  # suite unittest
├── sidecar/                # bridge WhatsApp (WhatsMeow)
├── scripts/                # utilitarios (fixtures providers)
├── examples/               # eventos de exemplo
├── Dockerfile              # imagem do serviço Sentinel
├── docker-compose.yml      # orquestracao padrao local
├── .env.example            # variaveis de ambiente de referencia
├── sentinel.toml           # configuracao principal
└── pyproject.toml          # pacote, scripts e ferramentas
```

## Modelo de dados (SQLite)

O schema foi desenhado para auditoria e reproducao de decisao. Tabelas:

| Tabela | Objetivo |
| --- | --- |
| `groups` | Cadastro de grupos por plataforma + id externo |
| `users` | Cadastro de usuarios por plataforma + id externo |
| `messages` | Mensagens ingeridas e metadados de origem |
| `audio_transcriptions` | Resultado de transcricao por mensagem de audio |
| `normalized_messages` | Texto normalizado para analise e buscas |
| `message_features` | Features por mensagem (ataque direto, caps, etc.) |
| `analysis_windows` | Janelas de contexto avaliadas |
| `window_messages` | Relacao N:N entre janela e mensagens |
| `window_features` | Features agregadas por janela |
| `llm_classifications` | Requisicao/resposta do classificador estruturado |
| `incident_assessments` | Decisao de risco final por janela |
| `alerts` | Alertas emitidos por canal |
| `moderator_feedback` | Feedback humano por incidente |
| `daily_reports` | Relatorio markdown + payload consolidado |
| `message_search` (FTS5) | Indice full-text do texto normalizado |

Indices de desempenho ja inclusos para consultas por grupo/tempo, feedback e correlacao de incidentes.

## Contratos de API

### `GET /healthz`

Resposta:

```json
{"status":"ok"}
```

### `POST /ingest`

Payload minimo (texto):

```json
{
  "platform": "demo",
  "external_group_id": "grupo-1",
  "external_user_id": "alice",
  "message_type": "text",
  "raw_text": "Voce esta distorcendo tudo de novo.",
  "received_at": "2026-04-18T16:00:11Z"
}
```

Regras de validacao:

- `message_type=text` exige `raw_text`.
- `message_type=audio` exige `media_path` **ou** `transcript_text`.
- Campos extras fora do modelo sao rejeitados (`extra="forbid"`).

Resposta (`IngestResult`):

```json
{
  "message_id": "msg_xxx",
  "group_id": "grp_xxx",
  "user_id": "usr_xxx",
  "assessment_id": "inc_xxx",
  "alert_ids": ["alt_xxx"],
  "severity": "tensao",
  "risk_score": 0.72
}
```

### `POST /feedback`

Payload:

```json
{
  "incident_id": "inc_123",
  "feedback_type": "correto",
  "note": "Bom sinal, contexto validado",
  "reviewer_id": "mod_1"
}
```

`feedback_type` aceitos:

- `correto`
- `exagerado`
- `incorreto`
- `util apesar de impreciso`
- `revisado manualmente`

### `POST /report-daily`

Payload:

```json
{
  "group_id": "grp_123",
  "date": "2026-04-18"
}
```

Resposta:

- `markdown`: relatorio pronto para leitura humana.
- `payload`: consolidado estruturado (totais, topicos e incidentes).

## CLI detalhada

Comando base:

```bash
PYTHONPATH=src python3 -m sentinel.cli --config sentinel.toml <subcomando>
```

Flags globais:

- `--env-file <caminho>` para usar outro arquivo dotenv.
- `--no-env-file` para nao carregar dotenv.

Subcomandos:

| Subcomando | Funcao |
| --- | --- |
| `init-db` | Cria/atualiza schema SQLite |
| `serve` | Sobe API HTTP |
| `ingest` | Ingestao de evento via arquivo JSON |
| `report-daily` | Gera relatorio diario por grupo |
| `feedback` | Registra feedback do moderador |

Exemplos:

```bash
PYTHONPATH=src python3 -m sentinel.cli init-db --db sentinel.db
PYTHONPATH=src python3 -m sentinel.cli serve --host 127.0.0.1 --port 8080
PYTHONPATH=src python3 -m sentinel.cli ingest --event-file examples/hostile-message.json
PYTHONPATH=src python3 -m sentinel.cli report-daily --group-id grp_demo --date 2026-04-18
PYTHONPATH=src python3 -m sentinel.cli feedback --incident-id inc_123 --feedback-type correto
```

## Configuracao completa (`sentinel.toml`)

### Matriz de campos

| Secao | Campo | Default | Uso |
| --- | --- | --- | --- |
| `[app]` | `db_path` | `sentinel.db` | Caminho do SQLite |
| `[server]` | `host` | `127.0.0.1` | Bind da API |
| `[server]` | `port` | `8080` | Porta da API |
| `[server]` | `auth_token` | `""` | Token Bearer opcional |
| `[windows]` | `short_minutes` | `5` | Janela curta por tempo |
| `[windows]` | `short_message_count` | `20` | Limite de msgs janela curta |
| `[windows]` | `expanded_minutes` | `15` | Janela expandida por tempo |
| `[windows]` | `expanded_message_count` | `50` | Limite de msgs janela expandida |
| `[heuristics]` | `llm_threshold` | `0.55` | A partir daqui abre classificacao estruturada |
| `[heuristics]` | `heuristic_only_threshold` | `0.85` | Limiar alternativo para caminho heuristico |
| `[heuristics]` | `weights` | ver arquivo | Pesos do score agregado |
| `[alerts]` | `channels` | `["stdout"]` | Canais de entrega |
| `[alerts]` | `cooldown_seconds` | `300` | Cooldown entre alertas de mesmo grupo |
| `[alerts]` | `minimum_severity` | `tensao` | Severidade minima para emitir alerta |
| `[transcription]` | `provider` | `groq` | Provider de transcricao |
| `[transcription]` | `model` | `whisper-large-v3-turbo` | Modelo de audio |
| `[transcription]` | `api_key_env` | `GROQ_API_KEY` | Env var da chave |
| `[transcription]` | `base_url` | Groq OpenAI URL | Endpoint base |
| `[transcription]` | `timeout_seconds` | `120` | Timeout transcricao |
| `[llm]` | `provider` | `gemini` | Classificador principal |
| `[llm]` | `model` | `gemini-2.5-flash` | Modelo LLM |
| `[llm]` | `api_key_env` | `GEMINI_API_KEY` | Env var da chave |
| `[llm]` | `base_url` | Gemini v1beta | Endpoint base |
| `[llm]` | `timeout_seconds` | `30` | Timeout classificacao |
| `[llm]` | `command` | `""` | Comando alternativo local (stdin/stdout JSON) |

### Observacao importante sobre thresholds

No fluxo atual do `service.py`, o ramo `heuristic_only_threshold` so executa quando:

`heuristic_only_threshold <= risk_score < llm_threshold`

Com defaults (`0.85` e `0.55`), esse intervalo nao existe. Se voce quiser ativar esse ramo explicitamente, configure `heuristic_only_threshold` menor ou igual a `llm_threshold`.

## Heuristicas e classificacao

### Features por mensagem

Extraidas em `compute_message_features`:

- `caps_ratio`
- `exclamation_count`
- `question_count`
- `direct_attack_score`
- `profanity_score`
- `sarcasm_hint_score`
- `imperative_score`
- `reply_intensity_score`
- `negativity_score`

### Features por janela

Extraidas em `compute_window_features`:

- `messages_per_minute`
- `reply_concentration_score`
- `dyadic_exchange_score`
- `participant_concentration_score`
- `escalation_velocity_score`
- `hostility_density_score`
- `sustained_back_and_forth_score`
- `audio_burst_score`
- `heuristic_risk_score` (combinacao ponderada)

### Severidade heuristica

- `< 0.35` -> `normal`
- `< 0.55` -> `atencao`
- `< 0.75` -> `tensao`
- `>= 0.75` -> `incendio`

### Classificacao estruturada

Ordem de tentativa:

1. Gemini (`provider=gemini` com chave valida)
2. comando externo (`llm.command`)
3. fallback heuristico interno

Resposta final e validada via `ClassificationResult` (Pydantic), incluindo consistencia de `trigger_message_id` dentro da janela.

## Alertas

Um alerta so e emitido quando todos os criterios passam:

1. severidade da classificacao >= `alerts.minimum_severity`;
2. cooldown por grupo satisfeito (`alerts.cooldown_seconds`), ou agravamento relevante de risco;
3. canal habilitado em `alerts.channels`.

Canal implementado nesta base:

- `stdout`: imprime versao humana + JSON estruturado.

Payload inclui participantes, severidade, resumo, mensagem gatilho e sinais operacionais.

## Sidecars WhatsApp

O sidecar documentado nesta base envia payload para o mesmo contrato `POST /ingest`.

### WhatsMeow (Go)

Principais variaveis:

| Variavel | Default |
| --- | --- |
| `SENTINEL_INGEST_URL` | `http://127.0.0.1:8080/ingest` |
| `SENTINEL_AUTH_TOKEN` | vazio |
| `SENTINEL_PLATFORM` | `whatsapp` |
| `SENTINEL_WHATSMEOW_STORE` | `./.store/whatsmeow.db` |
| `SENTINEL_MEDIA_DIR` | `./.media` |
| `SENTINEL_GROUPS_ONLY` | `true` |
| `SENTINEL_IGNORE_FROM_ME` | `true` |
| `SENTINEL_HTTP_TIMEOUT` | `30s` |
| `SENTINEL_RECONNECT_WAIT` | `5s` |
| `LOG_LEVEL` | `info` |

Diferenciais:

- armazenamento de sessao em SQLite local;
- reconexao controlada;
- QR no terminal para pareamento;
- download de audio com escrita atomica (remove arquivo parcial em falha).

## Desenvolvimento local

### Setup

```bash
uv sync --extra dev
```

### Subir com Docker Compose (padrao)

```bash
cp .env.example .env
docker compose up --build -d
```

### Validacoes

```bash
uv run python -m unittest discover -s tests
uv run --extra dev pyright
uv run --extra dev ruff check .
```

### Atualizar fixtures offline dos providers

```bash
GROQ_API_KEY=... GEMINI_API_KEY=... uv run python scripts/refresh_provider_fixtures.py
```

Comportamento do script:

- Groq: tenta atualizar fixture real; se falhar, grava fixture de erro (`groq_transcription_error_fixture.json`).
- Gemini: atualiza fixture de classificacao estruturada.

## Operacao e troubleshooting

### Erro `unauthorized` no servidor

- confirme `Authorization: Bearer <token>` quando `server.auth_token` estiver setado.

### Mensagens de audio sem texto

- verifique se `GROQ_API_KEY` esta disponivel;
- confirme se `media_path` e acessivel pelo processo do Sentinel;
- em fallback sem provider, transcricao fica `failed` e pipeline segue com texto vazio.

### Nenhum alerta emitido

- confira `alerts.minimum_severity`;
- confira `alerts.cooldown_seconds`;
- verifique se os eventos estao entrando no mesmo `group_id`.

### Risco alto, mas sem LLM externo

- valide `GEMINI_API_KEY` e `llm.provider=gemini`;
- se sem chave, Sentinel usa fallback heuristico por design.

## Limites conhecidos

- sem dashboard web embutido (saida em DB/JSON/stdout);
- sem envio ativo de mensagens de moderacao no WhatsApp;
- `detect_language` atual e fixo em `pt-BR`;
- threshold `heuristic_only_threshold` exige ajuste para ativacao efetiva, conforme nota acima.
