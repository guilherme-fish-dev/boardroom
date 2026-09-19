# Corte de loop de menções entre agentes

Data: 2026-09-18
Status: Implementado

## Contexto e motivação

Hoje, ser `@mencionado` sempre gera um job de resposta (`enqueue_mentions`, `app/routers/messages.py:53`), sem nenhuma checagem de "vale a pena responder?". O próprio prompt do sistema (`_mention_instructions`, `app/queue_worker.py:56`) incentiva o agente a mencionar outros para trazer opinião ou encadear respostas. Isso cria um problema real observado em uso: dois agentes ficam se mencionando em sequência (ex.: "concordo com o Leo" → Leo é mencionado, responde e menciona de volta → loop), consumindo a fila de jobs até o limite (`max_pending_per_group`) sem nenhum conteúdo novo.

A única guarda existente hoje é contra auto-menção (um agente não enfileira job pra si mesmo) e o limite global de fila por conversa — nenhum dos dois impede especificamente um loop A↔B entre dois agentes diferentes.

## Objetivo

- Impedir loops de menção mútua entre o mesmo par de agentes, sem exigir intervenção manual ou esperar a fila encher.
- Dar ao próprio modelo a opção de não responder quando a menção for só uma confirmação/concordância social sem conteúdo novo a acrescentar.
- Não afetar o caso normal: usuário mencionando agentes, ou um agente trazendo um terceiro agente para a conversa continuam funcionando exatamente como hoje.

Fora de escopo: configuração por grupo do limite de trocas (fixo em 3, hardcoded); UI que mostre que um agente "decidiu pular"; qualquer mudança de schema (a solução inteira usa dados já existentes em `messages`).

## Mecanismo 1: cooldown mecânico por par de agentes

Novo helper em `app/routers/messages.py`, usado dentro de `enqueue_mentions`:

```python
MAX_CONSECUTIVE_MENTION_EXCHANGES = 3  # 3 idas-e-voltas = 6 mensagens alternadas seguidas


def _pair_exchange_count(
    conn: sqlite3.Connection, conversation_id: int, agent_a: int, agent_b: int
) -> int:
    """Count how many of the most recent messages in the conversation form an unbroken,
    strictly alternating chain between agent_a and agent_b (starting from the newest message,
    which is expected to be agent_a's just-inserted reply). Any message from a third agent,
    from the user, or a system message breaks the chain at that point — which is exactly
    the "someone else joined, reset the count" behavior we want, with no extra bookkeeping."""
    rows = conn.execute(
        "SELECT sender_type, sender_id FROM messages WHERE conversation_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (conversation_id, MAX_CONSECUTIVE_MENTION_EXCHANGES * 2 + 1),
    ).fetchall()
    expected = agent_a
    other = agent_b
    count = 0
    for row in rows:
        if row["sender_type"] != "agent" or row["sender_id"] != expected:
            break
        count += 1
        expected, other = other, expected
    return count
```

Em `enqueue_mentions`, dentro do loop que já existe (`app/routers/messages.py:91-99`), a checagem só se aplica quando `author_agent_id is not None` (ou seja, quando quem está mencionando é um agente respondendo — menção vinda do usuário nunca é bloqueada, porque é sempre uma intervenção nova, não uma continuação do loop):

```python
for name in names:
    agent_id = agent_ids_by_name.get(name)
    if agent_id is None or agent_id == author_agent_id:
        continue
    if author_agent_id is not None and _pair_exchange_count(
        conn, conversation_id, author_agent_id, agent_id
    ) >= MAX_CONSECUTIVE_MENTION_EXCHANGES * 2:
        continue  # par em cooldown — última janela de mensagens é só troca entre os dois
    conn.execute(...)  # INSERT já existente
```

Nenhuma mensagem de sistema é inserida quando isso acontece — o job simplesmente não é criado, de forma silenciosa, consistente com a decisão de manter o SKIP também silencioso (mecanismo 2). Assim que um terceiro agente ou o usuário postar na conversa, a cadeia alternada quebra e a contagem volta a zero automaticamente, sem estado extra pra resetar.

## Mecanismo 2: o próprio modelo pode optar por não responder (SKIP)

Nova instrução fixa, acrescentada ao prompt de sistema em `_build_history` (`app/queue_worker.py:92`), junto das já existentes (`WEB_SEARCH_INSTRUCTIONS`, `_mention_instructions`):

```python
SKIP_INSTRUCTIONS = (
    "\n\nSe você foi mencionado apenas para confirmar, concordar ou reagir, e não tem "
    "nada de substância para acrescentar, responda usando SOMENTE isto, nada mais: [[SKIP]]. "
    "Isso significa que você optou por não responder e nenhuma mensagem sua será publicada."
)

SKIP_MARKER = "[[SKIP]]"
```

Em `_process_agent_turn` (`app/queue_worker.py:159`), depois que o loop de busca (`BUSCAR:`) termina e `reply` é a resposta final do modelo:

```python
if reply.strip().casefold() == SKIP_MARKER.casefold():
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
    return
```

Isso acontece antes do `INSERT INTO messages` e antes de `enqueue_mentions` — nenhuma mensagem é criada, nenhuma menção nova é extraída da resposta de SKIP, e a fila simplesmente perde um job sem deixar rastro visível na conversa. O restante do fluxo (`INSERT`, contagem de fila, `enqueue_mentions`) permanece igual para qualquer resposta que não seja exatamente o marcador.

A comparação exige que a resposta inteira (após `strip()`) seja o marcador — uma resposta como "Concordo. [[SKIP]]" não ativa o skip (o modelo já teria, nesse caso, escrito conteúdo de sobra; a instrução pede explicitamente "SOMENTE isto, nada mais"). Isso evita falso positivo de skip parcial em respostas que apenas citam o marcador.

## Interação entre os dois mecanismos

São independentes e se complementam:
- O cooldown mecânico (mecanismo 1) é a garantia dura: mesmo que o modelo nunca use `[[SKIP]]`, depois de 3 idas-e-voltas seguidas entre o mesmo par, novas menções entre os dois param de gerar job.
- O SKIP (mecanismo 2) evita gastar uma chamada de LLM inteira em "Concordo!" quando o próprio modelo já percebe que não há nada a acrescentar, mesmo dentro do limite de 3 trocas.

## Testes

Backend (`tests/test_messages.py` e/ou novo `tests/test_mention_loop_guard.py`):
- `_pair_exchange_count`: cadeia alternada pura entre A e B retorna a contagem correta; uma mensagem de um terceiro agente ou do usuário no meio interrompe a contagem no ponto certo; menos de `MAX_CONSECUTIVE_MENTION_EXCHANGES * 2` mensagens disponíveis não estoura (retorna o que houver).
- `enqueue_mentions`: com `author_agent_id` setado e histórico já no limite de cooldown, nenhum job é criado para o agente em cooldown, mas outro agente mencionado na mesma mensagem (fora do par em cooldown) continua recebendo job normalmente.
- `enqueue_mentions`: quando `author_agent_id is None` (mensagem do usuário), o cooldown nunca bloqueia, mesmo que o histórico recente pareça um loop.

`tests/test_queue_worker.py`:
- `_process_agent_turn` com `chat_completion` mockado retornando exatamente `"[[SKIP]]"` (e variações de espaço/caixa): nenhuma mensagem é inserida em `messages`, o job termina `done`, e `enqueue_mentions` não é chamado (nenhum novo job criado).
- Resposta contendo `[[SKIP]]` junto de outro texto não aciona o skip — vira mensagem normal.
- Teste de integração do loop completo: agente A menciona B, B menciona A, repetido até o limite — a 4ª tentativa de menção entre os dois não gera job novo (usa o cooldown mecânico), confirmando que o loop realmente para.

Sem mudança de frontend — o comportamento é inteiramente do backend/fila, sem UI nova.
