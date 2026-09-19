# Corte de loop de menções entre agentes — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que dois agentes fiquem se mencionando indefinidamente (loop A↔B), combinando um cooldown mecânico por par de agentes com a opção do próprio modelo de não responder (`[[SKIP]]`) quando a menção for só uma confirmação social sem conteúdo novo.

**Architecture:** Duas guardas independentes e complementares. (1) `_pair_exchange_count` em `app/routers/messages.py` conta, olhando as últimas mensagens da conversa, quantas seguidas alternam estritamente entre o autor e o agente mencionado; `enqueue_mentions` usa essa contagem para não criar job quando o par já trocou 3 idas-e-voltas seguidas. (2) Uma nova instrução de sistema em `app/queue_worker.py` ensina o modelo a responder `[[SKIP]]` quando não tem nada a acrescentar; `_process_agent_turn` detecta esse marcador exato e não publica mensagem nem encadeia novas menções.

**Tech Stack:** Python, FastAPI, SQLite (via `sqlite3` stdlib), pytest.

Spec de referência: `docs/superpowers/specs/2026-09-18-mention-loop-guard-design.md`.

---

### Task 1: `_pair_exchange_count` — cooldown mecânico por par de agentes

**Files:**
- Modify: `app/routers/messages.py`
- Test: `tests/test_messages_api.py`

- [ ] **Step 1: Escrever os testes que falham para `_pair_exchange_count`**

Adicionar ao final de `tests/test_messages_api.py`:

```python
from app.routers.messages import _pair_exchange_count


def test_pair_exchange_count_counts_pure_alternating_chain(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    conn = get_connection()
    try:
        # Cadeia alternada mais recente primeiro (na inserção, mais antiga primeiro):
        # bob, alice, bob, alice, bob, alice — 6 mensagens, 3 idas-e-voltas completas.
        for i, agent_id in enumerate([bob["id"], alice["id"]] * 3):
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
                "VALUES (?, 'agent', ?, ?)",
                (conversation["id"], agent_id, f"msg {i}"),
            )
        conn.commit()
        count = _pair_exchange_count(conn, conversation["id"], alice["id"], bob["id"])
    finally:
        conn.close()

    assert count == 6


def test_pair_exchange_count_stops_at_third_party_message(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    carla = client.post(
        "/api/agents",
        json={"name": "carla", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": carla["id"]})

    conn = get_connection()
    try:
        # Mais antiga -> mais recente: carla quebra a cadeia, depois bob/alice alternam 2x.
        for agent_id, content in [
            (carla["id"], "intrusa"),
            (bob["id"], "m1"),
            (alice["id"], "m2"),
            (bob["id"], "m3"),
            (alice["id"], "m4"),
        ]:
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
                "VALUES (?, 'agent', ?, ?)",
                (conversation["id"], agent_id, content),
            )
        conn.commit()
        count = _pair_exchange_count(conn, conversation["id"], alice["id"], bob["id"])
    finally:
        conn.close()

    assert count == 4


def test_pair_exchange_count_returns_available_length_when_short(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'agent', ?, 'oi')",
            (conversation["id"], alice["id"]),
        )
        conn.commit()
        count = _pair_exchange_count(conn, conversation["id"], alice["id"], bob["id"])
    finally:
        conn.close()

    assert count == 1
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_messages_api.py -k pair_exchange_count -v`
Expected: FAIL com `ImportError: cannot import name '_pair_exchange_count'`

- [ ] **Step 3: Implementar `_pair_exchange_count` em `app/routers/messages.py`**

Adicionar logo antes da função `enqueue_mentions` (depois de `_row_to_message`, por volta da linha 51):

```python
MAX_CONSECUTIVE_MENTION_EXCHANGES = 3  # 3 idas-e-voltas = 6 mensagens alternadas seguidas


def _pair_exchange_count(
    conn: sqlite3.Connection, conversation_id: int, agent_a: int, agent_b: int
) -> int:
    """Count how many of the most recent messages in the conversation form an unbroken,
    strictly alternating chain between agent_a and agent_b (starting from the newest message,
    which is expected to be agent_a's just-inserted reply). Any message from a third agent,
    from the user, or a system message breaks the chain at that point — which is exactly the
    "someone else joined, reset the count" behavior we want, with no extra bookkeeping."""
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

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_messages_api.py -k pair_exchange_count -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: add _pair_exchange_count helper for mention loop cooldown"
```

---

### Task 2: usar o cooldown em `enqueue_mentions`

**Files:**
- Modify: `app/routers/messages.py:53-99`
- Test: `tests/test_messages_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar a `tests/test_messages_api.py`:

```python
def test_enqueue_mentions_blocks_pair_in_cooldown(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    conn = get_connection()
    try:
        # 6 mensagens alternadas seguidas entre bob e alice (3 idas-e-voltas) já na conversa.
        for agent_id in [bob["id"], alice["id"]] * 3:
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
                "VALUES (?, 'agent', ?, 'oi')",
                (conversation["id"], agent_id),
            )
        conn.commit()

        # A "sétima" mensagem: bob menciona alice de novo — deveria ser bloqueada pelo cooldown.
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'agent', ?, '@alice de novo?')",
            (conversation["id"], bob["id"]),
        )
        trigger_id = cur.lastrowid
        conn.commit()

        from app.routers.messages import enqueue_mentions

        enqueue_mentions(
            conn, conversation["id"], trigger_id, "@alice de novo?", author_agent_id=bob["id"]
        )
        conn.commit()

        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()

    assert jobs == []


def test_enqueue_mentions_pair_cooldown_does_not_affect_other_mentioned_agent(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    carla = client.post(
        "/api/agents",
        json={"name": "carla", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": carla["id"]})

    conn = get_connection()
    try:
        for agent_id in [bob["id"], alice["id"]] * 3:
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
                "VALUES (?, 'agent', ?, 'oi')",
                (conversation["id"], agent_id),
            )
        conn.commit()

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'agent', ?, '@alice @carla e ai?')",
            (conversation["id"], bob["id"]),
        )
        trigger_id = cur.lastrowid
        conn.commit()

        from app.routers.messages import enqueue_mentions

        enqueue_mentions(
            conn, conversation["id"], trigger_id, "@alice @carla e ai?", author_agent_id=bob["id"]
        )
        conn.commit()

        jobs = conn.execute("SELECT agent_id FROM queue_jobs").fetchall()
    finally:
        conn.close()

    assert [j["agent_id"] for j in jobs] == [carla["id"]]


def test_enqueue_mentions_from_user_never_blocked_by_cooldown(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    conn = get_connection()
    try:
        for agent_id in [bob["id"], alice["id"]] * 3:
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
                "VALUES (?, 'agent', ?, 'oi')",
                (conversation["id"], agent_id),
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "@bob @alice o que acham?"}
    )
    message = resp.json()

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT agent_id FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert {j["agent_id"] for j in jobs} == {bob["id"], alice["id"]}
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_messages_api.py -k cooldown -v`
Expected: FAIL — `test_enqueue_mentions_blocks_pair_in_cooldown` e `test_enqueue_mentions_pair_cooldown_does_not_affect_other_mentioned_agent` falham porque o job ainda é criado (nenhum cooldown implementado ainda).

- [ ] **Step 3: Implementar a checagem em `enqueue_mentions`**

Em `app/routers/messages.py`, dentro do loop existente (linhas 91-99), adicionar a checagem de cooldown:

```python
    for name in names:
        agent_id = agent_ids_by_name.get(name)
        if agent_id is None or agent_id == author_agent_id:
            continue
        if author_agent_id is not None and _pair_exchange_count(
            conn, conversation_id, author_agent_id, agent_id
        ) >= MAX_CONSECUTIVE_MENTION_EXCHANGES * 2:
            continue
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (conversation_id, agent_id, json.dumps({"trigger_message_id": trigger_message_id})),
        )
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_messages_api.py -v`
Expected: PASS (todos os testes de `test_messages_api.py`, incluindo os 3 novos de cooldown e os já existentes)

- [ ] **Step 5: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: apply mention loop cooldown in enqueue_mentions"
```

---

### Task 3: instrução `[[SKIP]]` e detecção em `_process_agent_turn`

**Files:**
- Modify: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar a `tests/test_queue_worker.py`:

```python
def test_process_next_job_skip_marker_posts_no_message(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob concordo')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "  [[SKIP]]  ")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute("SELECT * FROM messages WHERE sender_type = 'agent'").fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert agent_messages == []
    assert job["status"] == "done"


def test_process_next_job_skip_marker_case_insensitive(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob concordo')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "[[skip]]")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute("SELECT * FROM messages WHERE sender_type = 'agent'").fetchall()
    finally:
        conn.close()
    assert agent_messages == []


def test_process_next_job_skip_marker_with_extra_text_is_not_treated_as_skip(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob concordo')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "Concordo. [[SKIP]]")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute("SELECT * FROM messages WHERE sender_type = 'agent'").fetchall()
    finally:
        conn.close()
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Concordo. [[SKIP]]"


def test_process_next_job_skip_marker_does_not_enqueue_follow_up(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    alice_id = _create_agent(conn, name="alice")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob concordo')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.queue_worker.enqueue_mentions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria ser chamado")),
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "[[SKIP]]")

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()
    assert job["status"] == "done"


def test_process_next_job_system_prompt_includes_skip_instructions(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "olá",
    )

    process_next_job()

    system_content = calls[0]["messages"][0]["content"]
    assert "[[SKIP]]" in system_content
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_queue_worker.py -k skip -v`
Expected: FAIL — `test_process_next_job_skip_marker_posts_no_message` e as demais falham porque hoje `"[[SKIP]]"` viraria uma mensagem normal (nenhuma lógica de skip existe ainda).

- [ ] **Step 3: Implementar a instrução e a detecção em `app/queue_worker.py`**

Adicionar as constantes logo depois de `WEB_SEARCH_INSTRUCTIONS` (por volta da linha 53):

```python
SKIP_INSTRUCTIONS = (
    "\n\nSe você foi mencionado apenas para confirmar, concordar ou reagir, e não tem "
    "nada de substância para acrescentar, responda usando SOMENTE isto, nada mais: [[SKIP]]. "
    "Isso significa que você optou por não responder e nenhuma mensagem sua será publicada."
)

SKIP_MARKER = "[[SKIP]]"
```

Em `_build_history` (linha 107), incluir `SKIP_INSTRUCTIONS` na montagem do prompt de sistema:

```python
    system_content = (
        agent_persona + WEB_SEARCH_INSTRUCTIONS + SKIP_INSTRUCTIONS + _mention_instructions(other_names)
    )
```

Em `_process_agent_turn` (`app/queue_worker.py:159`), logo depois do bloco do loop de busca (depois da linha 217, antes do `cur = conn.execute("INSERT INTO messages ...")` na linha 219), adicionar a checagem do marcador:

```python
    if reply.strip().casefold() == SKIP_MARKER.casefold():
        conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
        return

    cur = conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
        "VALUES (?, 'agent', ?, ?)",
        (job["conversation_id"], agent["id"], reply),
    )
```

(A linha `cur = conn.execute(...)` e tudo depois dela já existem sem alteração — o único código novo é o bloco `if reply.strip()...return` inserido imediatamente antes dessa linha.)

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_queue_worker.py -v`
Expected: PASS (todos os testes de `test_queue_worker.py`, incluindo os 5 novos de skip e os já existentes)

- [ ] **Step 5: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: let agents opt out of replying with a [[SKIP]] marker"
```

---

### Task 4: teste de integração ponta a ponta do loop sendo cortado

**Files:**
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Escrever o teste de integração**

Adicionar a `tests/test_queue_worker.py`:

```python
def test_mention_loop_between_two_agents_is_cut_off_by_cooldown(db, monkeypatch):
    """Fim a fim: bob e alice ficam se mencionando mutuamente. Depois de 3 idas-e-voltas
    completas (6 mensagens de agente alternadas), o cooldown mecânico (Task 2) impede que a
    próxima menção mútua gere um novo job — sem depender do [[SKIP]] do modelo (Task 3), que
    aqui nunca é usado."""
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    alice_id = _create_agent(conn, name="alice")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob e ai?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
    )
    conn.commit()
    conn.close()

    # bob e alice se mencionam mutuamente pra sempre, se deixados sem guarda. A primeira
    # chamada é de bob respondendo ao usuário; a partir daí alterna bob->alice->bob->alice...,
    # cada um mencionando o outro de volta — um contador de chamadas é suficiente pra decidir
    # de quem é a vez, sem precisar inferir isso a partir dos kwargs do chat_completion.
    call_count = {"n": 0}

    def _fake_chat_completion(**kwargs):
        call_count["n"] += 1
        return "@alice concordo" if call_count["n"] % 2 == 1 else "@bob concordo"

    monkeypatch.setattr("app.queue_worker.chat_completion", _fake_chat_completion)

    # Processa jobs até a fila esvaziar (ou um teto de segurança bem acima do esperado).
    for _ in range(30):
        if not process_next_job():
            break

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT sender_id FROM messages WHERE sender_type = 'agent' ORDER BY id"
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE status = 'pending'"
        ).fetchone()["c"]
    finally:
        conn.close()

    # 6 mensagens de agente (3 idas-e-voltas) e nem uma a mais: a 7ª menção mútua foi bloqueada
    # pelo cooldown, então a fila esvazia sozinha em vez de continuar indefinidamente.
    assert len(agent_messages) == 6
    assert pending == 0
```

(Remova a linha antiga de `monkeypatch.setattr` com `_last_is_bob` e a referência a essa função auxiliar inexistente — use somente o `_fake_chat_completion` acima.)

- [ ] **Step 2: Rodar o teste e confirmar que passa**

Run: `pytest tests/test_queue_worker.py -k mention_loop_between_two_agents -v`
Expected: PASS — com as Tasks 1-3 já implementadas, o cooldown mecânico corta o loop na 4ª tentativa de menção mútua (6 mensagens de agente, fila vazia).

Se esse teste FALHAR (loop não corta), o bug mais provável é a ordem de aplicação do cooldown em `enqueue_mentions` — confirme que `_pair_exchange_count` está sendo chamado com `(author_agent_id, agent_id)` na ordem certa e que a query em `_process_agent_turn` já commitou a mensagem do agente atual (`cur.lastrowid`) antes de chamar `enqueue_mentions`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_queue_worker.py
git commit -m "test: verify end-to-end that mention loop cooldown stops A/B loops"
```

---

### Task 5: atualizar a spec com o status final e rodar a suíte completa

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-mention-loop-guard-design.md`

- [ ] **Step 1: Rodar a suíte de testes inteira**

Run: `pytest -v`
Expected: PASS — todos os testes do projeto, incluindo os novos das Tasks 1-4.

- [ ] **Step 2: Atualizar o status no topo da spec**

Em `docs/superpowers/specs/2026-09-18-mention-loop-guard-design.md`, trocar a linha:

```
Status: Aprovado para planejamento
```

por:

```
Status: Implementado
```

- [ ] **Step 3: Commit final**

```bash
git add docs/superpowers/specs/2026-09-18-mention-loop-guard-design.md
git commit -m "docs: mark mention loop guard spec as implemented"
```
