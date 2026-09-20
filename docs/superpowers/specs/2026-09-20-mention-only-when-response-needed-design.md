# Menção `@nome` só quando resposta é necessária

Data: 2026-09-20
Status: Implementado

## Contexto e motivação

Hoje, `_mention_instructions` (`app/queue_worker.py:91`) instrui o agente a mencionar outros com `@nome-exato` "para trazer a opinião deles pra conversa ou encadear uma sequência de respostas" — mas não distingue entre citar o que outro agente disse (ex.: "concordo com o que a Ana falou") e efetivamente querer que aquele agente responda (ex.: "@Ana, pode confirmar esse número?"). Como `extract_mentions` (`app/mentions.py`) só faz um scan textual de `@nome`, qualquer uso do `@` — mesmo em prosa, só citando alguém — dispara um job de resposta (`enqueue_mentions`), consumindo um turno de LLM inteiro sem necessidade.

Isso desperdiça contexto e tempo: um agente que só queria dizer "como a Ana mencionou antes, ..." acaba fazendo a Ana ser chamada de volta à conversa sem ter nada de novo a acrescentar (ela provavelmente vai só `[[SKIP]]`, mas isso já custou uma chamada ao modelo).

## Objetivo

Reduzir o número de jobs gerados por menções desnecessárias, ensinando o próprio modelo a distinguir os dois casos:
- **Precisa de resposta/ação** → usar `@nome` (ex.: pedir validação, fazer uma pergunta direta, encadear a conversa para outro agente continuar).
- **Só está citando/referenciando** o que outro agente disse ou concordando com ele → escrever o nome **sem** `@`.

Fora de escopo: qualquer enforcement mecânico dessa regra (checar se o texto ao redor do `@nome` "parece" uma pergunta, por exemplo). O cooldown de 3 trocas por par (`_pair_exchange_count`) e o `[[SKIP]]` já existentes continuam sendo a rede de segurança para quando o modelo não seguir a instrução — esta mudança é só sobre reduzir a frequência do problema na origem (o modelo gerando a menção desnecessária), não sobre impedir suas consequências (isso já está coberto).

## Mudança: `_mention_instructions` em `app/queue_worker.py`

Substituir o texto atual (`app/queue_worker.py:91-101`):

```python
def _mention_instructions(other_agent_names: list[str]) -> str:
    if not other_agent_names:
        return ""
    names_list = ", ".join(f"@{name}" for name in other_agent_names)
    return (
        "\n\nVocê também pode mencionar outros agentes deste grupo escrevendo @nome-exato "
        "em qualquer parte da sua resposta, para trazer a opinião deles pra conversa ou "
        "encadear uma sequência de respostas (ex.: pedir pra outro agente validar ou "
        "continuar o que você disse). Use o nome exato cadastrado do agente. "
        f"Agentes deste grupo que você pode mencionar: {names_list}."
    )
```

Por:

```python
def _mention_instructions(other_agent_names: list[str]) -> str:
    if not other_agent_names:
        return ""
    names_list = ", ".join(f"@{name}" for name in other_agent_names)
    return (
        "\n\nVocê também pode mencionar outros agentes deste grupo escrevendo @nome-exato "
        "em qualquer parte da sua resposta — mas cada menção com @ aciona uma resposta "
        "completa daquele agente, o que custa tempo e contexto. Use @nome SOMENTE quando "
        "você realmente precisa que aquele agente responda ou aja agora (pedir validação, "
        "fazer uma pergunta direta a ele, ou encadear a conversa para ele continuar). "
        "Quando só quiser citar, comentar ou concordar com algo que outro agente já disse, "
        "escreva o nome dele SEM o @ — isso não aciona nada. "
        'Exemplo de menção correta (precisa de ação): "@Ana, pode confirmar esse número '
        'antes de eu continuar?" '
        'Exemplo de referência correta (não precisa de ação, sem @): "Concordo com o que '
        'a Ana falou sobre o orçamento." '
        "Use o nome exato cadastrado do agente quando for mencionar com @. "
        f"Agentes deste grupo que você pode mencionar: {names_list}."
    )
```

Nenhuma outra função muda. `enqueue_mentions`, `_pair_exchange_count`, `SKIP_INSTRUCTIONS`, `extract_mentions` continuam exatamente como estão — a extração de menções continua sendo um scan textual puro de `@nome`; a diferença é inteiramente no que o modelo decide escrever.

## Testes

`tests/test_queue_worker.py`: um teste garantindo que o prompt de sistema contém a nova orientação (mesmo padrão de `test_process_next_job_system_prompt_includes_skip_instructions` e `test_process_next_job_system_prompt_lists_other_group_agents_for_mentioning`) — verificar a presença de trechos-chave da instrução (ex.: "SOMENTE quando", "SEM o @") no `system_content` passado ao `chat_completion`, e que o exemplo de menção/referência aparece.

Não há como testar automaticamente que o modelo *de fato* segue a instrução melhor (isso depende do comportamento real do LLM em produção, fora do controle de testes unitários) — o teste garante apenas que a instrução correta chega ao prompt.

Sem mudança de schema, endpoint, frontend, ou qualquer outro arquivo.
