# Integracao HTTP do Sentinel

Este documento detalha o contrato usado entre sidecar e pipeline principal.

## De onde vem o JSON de ingestao

No fluxo principal, o sidecar WhatsMeow recebe `*events.Message`, converte para o payload de ingestao e envia para `POST /ingest`.

Resumo do caminho:

1. `sidecar/whatsmeow/main.go` recebe evento do WhatsApp.
2. `buildPayload` monta o objeto `ingestPayload`.
3. `postIngest` envia o JSON para o Sentinel.
4. O Sentinel valida esse JSON em `IncomingMessage` (`src/sentinel/models.py`).

## Payload de ingestao (`POST /ingest`)

Exemplo minimo para texto:

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

Para audio:

- use `message_type = "audio"`;
- informe `media_path` (arquivo local do sidecar), ou `transcript_text` quando a transcricao ja vier pronta;
- se `transcript_text` vier preenchido, o Sentinel nao chama o provider de transcricao.

Regras de validacao:

- `message_type=text` exige `raw_text`;
- `message_type=audio` exige `media_path` ou `transcript_text`;
- campos extras fora do modelo sao rejeitados.

Resposta de ingestao (`IngestResult`):

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

## Endpoints

- `GET /healthz`
- `POST /ingest`
- `POST /feedback`
- `POST /report-daily`

Se `server.auth_token` estiver configurado, enviar:

```http
Authorization: Bearer <token>
```

## Exemplo de fluxo completo

1. Sidecar envia mensagens para `POST /ingest`.
2. Sentinel processa texto/transcricao.
3. Sentinel classifica risco de escalada.
4. Sentinel persiste e disponibiliza consolidado no relatorio diario.
