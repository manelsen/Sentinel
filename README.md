# Sentinel

Sentinel e um pipeline de moderacao conversacional com sidecar WhatsMeow, transcricao de audio, avaliacao de risco e relatorio diario.

## Fluxo principal

1. **WhatsMeow recebe a mensagem** no WhatsApp.
2. **Sentinel processa o conteudo**: texto direto ou transcricao de audio.
3. **Sentinel avalia chance de escalada** (heuristica + classificacao estruturada).
4. **Sentinel persiste, alerta e consolida relatorio**.

## Execucao padrao (`docker compose`)

```bash
cp .env.example .env
docker compose up --build -d
```

Checagem de saude:

```bash
curl -s http://127.0.0.1:8080/healthz
```

## Configuracao minima

Preencha no `.env`:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `SENTINEL_AUTH_TOKEN` (opcional)

## Documentacao detalhada

- Contrato de ingestao e endpoints HTTP: `docs/integracao-http.md`
- Configuracao da aplicacao: `sentinel.toml`
