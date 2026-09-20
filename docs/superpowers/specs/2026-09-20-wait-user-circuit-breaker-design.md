# Trava mecânica para "aguardando usuário" e heurístico ampliado

Data: 2026-09-20
Status: Aprovado para planejamento

## Contexto e motivação

O mecanismo existente de "aguardando usuário" (`WAIT_USER_INSTRUCTIONS`, `WAIT_USER_MARKERS`, heurístico em `app/queue_worker.py:322-334`) deveria pausar a fila quando um agente pede uma decisão do usuário, mas falha na prática de duas formas observadas em uso real:

1. **Detecção não dispara em vários casos reais.** O modelo às vezes não termina a resposta com `[[AGUARDANDO_USUARIO]]` mesmo pedindo claramente uma decisão (ex.: "escolha entre as opções 1, 2 ou 3"), e o heurístico de fallback só reconhece o padrão de "preencher campo em branco" (`R\$\s*_{3,}|_{5,}`), não o de "escolher entre opções numeradas". Sem nenhum dos dois disparando, a fila nunca pausa.
2. **Mesmo quando um agente já pediu a decisão, um segundo agente pode reacender a conversa** escrevendo `@nome` num contexto condicional/futuro (ex.: "quando você escolher, @Ana vai analisar"). Como `extract_mentions` só faz um scan textual, isso cria um novo job pra Ana imediatamente — mesmo a instrução de hoje sobre "só mencionar quando precisa de ação" (`docs/superpowers/specs/2026-09-20-mention-only-when-response-needed-design.md`) não cobre esse uso ambíguo (referência a uma ação futura, condicionada à resposta do usuário).

O resultado observado: vários agentes respondem em sequência, cada um pedindo essencialmente a mesma decisão do usuário com palavras diferentes — gasto de contexto/tempo sem necessidade e poluição visual da conversa.

## Objetivo

Duas mudanças complementares, ambas em `app/queue_worker.py`:

1. **Ampliar o heurístico de fallback** para reconhecer também o padrão de "pedir pra escolher entre opções numeradas", além do já existente "preencher campo em branco".
2. **Adicionar uma trava mecânica (circuit breaker)**: antes de processar qualquer job `agent_turn`, verificar se a conversa já está "aguardando o usuário" (a mensagem visível mais recente é de um agente com `hidden_kind='wait_user'`, e nenhuma mensagem do usuário chegou depois). Se estiver, o job é finalizado imediatamente como `done`, **sem chamar o modelo** — nenhuma mensagem nova é criada, nenhum custo de LLM é gasto. Isso funciona mesmo quando a detecção falha em turnos específicos, porque basta UMA mensagem no histórico recente ter sido corretamente marcada pra travar todos os turnos seguintes até o usuário responder.

Fora de escopo: mudar a lógica de retomada quando o usuário efetivamente responde (`_maybe_enqueue_waiting_agent`, em `app/routers/messages.py`, já funciona corretamente — não muda); tornar a detecção 100% infalível (heurísticos de texto sempre têm exceções; a trava mecânica é o que garante robustez mesmo com detecção imperfeita, não o heurístico em si); mudar `_mention_instructions` de novo (o uso ambíguo do Milton — "@Ana vai agir quando você decidir" — é tratado indiretamente pela trava, não por uma nova regra de menção).

## Mudança 1: heurístico ampliado

Em `app/queue_worker.py`, a checagem atual (dentro de `_process_agent_turn`):

```python
    # Fallback heurístico: se há campos de preenchimento (ex: 'R$ _____') direcionados ao usuário
    if not needs_user_action and re.search(r"R\$\s*_{3,}|_{5,}", reply):
        needs_user_action = True
```

Passa a usar um padrão combinado, extraído para uma constante de módulo (ao lado de `WAIT_USER_MARKERS`):

```python
WAIT_USER_HEURISTIC_PATTERN = re.compile(
    r"R\$\s*_{3,}"                                    # campo de preenchimento (ex.: R$ _____)
    r"|_{5,}"                                          # linha de preenchimento genérica
    r"|escolh[ae]\s+(uma\s+)?(dessas|dessa|das)?\s*op[cç][õo]es"  # "escolha uma dessas opções"
    r"|digite\s+(o\s+n[uú]mero|sua\s+escolha)"         # "digite o número" / "digite sua escolha"
    r"|qual\s+(voc[eê]\s+)?(escolhe|prefere|ser[aá])"  # "qual você escolhe/prefere/será"
    r"|aguardando\s+(sua|a\s+sua)\s+(decis[aã]o|escolha|resposta)",  # "aguardando sua decisão"
    re.IGNORECASE,
)
```

E a checagem vira:

```python
    if not needs_user_action and WAIT_USER_HEURISTIC_PATTERN.search(reply):
        needs_user_action = True
```

## Mudança 2: trava mecânica antes de processar `agent_turn`

Novo helper em `app/queue_worker.py`, ao lado de `_other_group_agent_names`:

```python
def _conversation_is_awaiting_user(conn: sqlite3.Connection, conversation_id: int) -> bool:
    """True if the most recent visible message in the conversation is an agent's turn that
    flagged hidden_kind='wait_user' — i.e. someone already asked the user to decide/respond,
    and nothing (not even the user) has spoken since. Mirrors the check in
    app.routers.messages._maybe_enqueue_waiting_agent, which uses this same state to decide
    whether to resume the waiting agent once the user does reply."""
    last_msg = conn.execute(
        "SELECT sender_type, hidden_kind FROM messages WHERE conversation_id = ? "
        "AND hidden = 0 ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return bool(
        last_msg and last_msg["sender_type"] == "agent" and last_msg["hidden_kind"] == "wait_user"
    )
```

Em `_process_agent_turn` (`app/queue_worker.py`), logo no início da função (antes de montar o histórico e chamar o modelo — o objetivo é economizar exatamente essa chamada):

```python
def _process_agent_turn(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    if _conversation_is_awaiting_user(conn, job["conversation_id"]):
        conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
        return

    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (job["agent_id"],)).fetchone()
    # ... resto da função inalterado
```

Nenhuma mensagem é criada quando isso acontece — silencioso, consistente com a decisão já tomada pro `[[SKIP]]` (não poluir a conversa com avisos). O job simplesmente termina `done` sem nunca chamar `chat_completion`.

Esta checagem só se aplica a jobs `agent_turn` — `describe_image` e `extract_pdf` continuam processando normalmente mesmo com a conversa "aguardando usuário" (um PDF ou imagem anexados nesse meio-tempo ainda devem ser processados; é a resposta de outro agente que não faz sentido, não o processamento de anexos).

## Interação com o cancelamento reativo já existente

O cancelamento reativo já existente (linhas ~338-344: quando um agente flagra `needs_user_action`, todos os jobs `pending` da conversa são marcados `error` imediatamente) continua existindo e não muda — ele purga o backlog no exato momento em que a marcação acontece, minimizando o atraso. A trava mecânica desta mudança é uma segunda camada: cobre jobs que são criados **depois** desse momento (ex.: um `@nome` disparado por engano numa resposta futura, como no caso do Milton), que o cancelamento reativo não alcança porque ainda não existiam quando ele rodou.

## Testes

`tests/test_queue_worker.py`:
- Heurístico ampliado: `_process_agent_turn` com `chat_completion` mockado retornando um texto que pede escolha entre opções numeradas (ex.: `"Escolha uma dessas opções: 1... 2... 3..."`), sem a tag explícita — mensagem salva com `hidden_kind == 'wait_user'`.
- Trava mecânica bloqueia novo turno: histórico da conversa já tem uma mensagem de agente com `hidden_kind='wait_user'` como a mais recente visível; um novo job `agent_turn` (de qualquer agente) é processado e termina `done` **sem** chamar `chat_completion` (mock configurado pra levantar `AssertionError` se for chamado) e **sem** inserir mensagem nova.
- Trava mecânica não bloqueia quando o usuário já respondeu: mesmo cenário anterior, mas com uma mensagem do usuário inserida depois da mensagem `wait_user` — o job `agent_turn` roda normalmente (chama `chat_completion`).
- Trava mecânica não bloqueia jobs `describe_image`/`extract_pdf`: com uma mensagem `wait_user` mais recente na conversa, um job desses tipos ainda processa normalmente.
- Regressão: os testes já existentes de wait_user (`test_process_agent_turn_wait_user_tag_cancels_pending_jobs_and_suppresses_mentions`, `test_process_agent_turn_wait_user_heuristic_cancels_pending_jobs`, `test_process_agent_turn_prompt_includes_wait_user_and_anti_repeat_instructions`) continuam passando sem alteração.

Sem mudança de schema, endpoint, ou frontend.
