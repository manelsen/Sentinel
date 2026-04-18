from __future__ import annotations

import json

from .models import PromptBundle, WindowSnapshot

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """Voce e um classificador de risco relacional para moderacao de grupos.

Sua tarefa nao e decidir quem esta certo, quem esta errado, quem e moralmente culpado ou qual lado tem a verdade.
Sua tarefa e avaliar se existe risco de escalada relacional na janela de mensagens fornecida
e se um moderador humano deve ser alertado.

Criterios:
1. foco em escalada, hostilidade, provocacao, deboche, acusacao, defesa agressiva e intensificacao do tom;
2. contexto importa mais do que mensagem isolada;
3. brincadeira interna pode parecer agressiva, entao expresse incerteza quando necessario;
4. seja conservador: so classifique como incendio quando houver forte indicio de escalada relevante;
5. identifique participantes centrais e mensagem gatilho provavel quando possivel;
6. sua saida DEVE ser apenas JSON valido, sem markdown, sem explicacoes fora do JSON.
"""


def build_prompt(window_snapshot: WindowSnapshot) -> PromptBundle:
    metadata = window_snapshot["metadata"]
    messages = window_snapshot["messages"]
    messages_block = "\n".join(
        f"- {item['timestamp']} | {item['author_name']} | {item['message_id']} | {item['text']}" for item in messages
    )
    user_prompt = f"""Analise a janela abaixo.

Definicoes de severidade:
- normal: sem sinal relevante de conflito;
- atencao: sinais leves ou ambiguos, sem urgencia;
- tensao: sinais consistentes de escalada ou hostilidade, moderador deve ser avisado;
- incendio: escalada forte, rapida ou sustentada, moderador deve ser alertado imediatamente.

Metadados da janela:
- group_id: {metadata["group_id"]}
- window_id: {metadata["window_id"]}
- start_at: {metadata["start_at"]}
- end_at: {metadata["end_at"]}
- message_count: {metadata["message_count"]}
- distinct_user_count: {metadata["distinct_user_count"]}
- heuristic_risk_score: {metadata["heuristic_risk_score"]}
- heuristic_signals: {json.dumps(metadata["heuristic_signals"], ensure_ascii=True)}

Mensagens em ordem cronologica:
{messages_block}

Retorne JSON com os campos: conflict_present, escalation_risk, severity, participants,
trigger_message_id, evidence, summary_short, summary_long, recommended_action, confidence,
uncertainty_notes
"""
    return PromptBundle(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        request_payload={
            "prompt_version": PROMPT_VERSION,
            "metadata": metadata,
            "messages": messages,
        },
    )
