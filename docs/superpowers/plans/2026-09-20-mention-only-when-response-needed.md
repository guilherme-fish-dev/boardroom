# Menção `@nome` só quando resposta é necessária — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reescrever a instrução de menção que os agentes recebem no prompt de sistema para deixar explícito que `@nome` só deve ser usado quando o agente citado precisa responder/agir — não para simplesmente referenciar ou concordar com algo que ele já disse — reduzindo jobs de resposta desnecessários.

**Architecture:** Mudança isolada em uma única função (`_mention_instructions`, `app/queue_worker.py`) que monta o texto de instrução injetado no prompt de sistema de cada agente. Nenhuma outra parte do sistema (extração de menções, enfileiramento, cooldown, SKIP) muda.

**Tech Stack:** Python, pytest.

Spec de referência: `docs/superpowers/specs/2026-09-20-mention-only-when-response-needed-design.md`.

---

### Task 1: reescrever `_mention_instructions` com a regra de menção condicional

**Files:**
- Modify: `app/queue_worker.py:91-101`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `tests/test_queue_worker.py`:

```python
def test_process_next_job_mention_instructions_explain_when_to_use_at_sign(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    alice_id = _create_agent(conn, name="alice")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
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
    assert "SOMENTE quando" in system_content
    assert "SEM o @" in system_content
    assert "@Ana, pode confirmar esse número" in system_content
    assert "Concordo com o que a Ana falou" in system_content
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_queue_worker.py -k mention_instructions_explain -v`
Expected: FAIL — os trechos novos (`"SOMENTE quando"`, `"SEM o @"`, os dois exemplos) ainda não existem no texto atual de `_mention_instructions`, então as asserções falham.

- [ ] **Step 3: Reescrever `_mention_instructions` em `app/queue_worker.py`**

Substituir a função inteira (linhas 91-101):

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

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_queue_worker.py -k mention_instructions_explain -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa para checar ausência de regressão**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — todos os testes do projeto, incluindo os dois testes já existentes que checam o conteúdo do prompt de menção (`test_process_next_job_system_prompt_lists_other_group_agents_for_mentioning`, que verifica que `"@alice"` e `"mencionar outros agentes"` continuam presentes — ambos os trechos sobrevivem na nova versão do texto — e `test_process_next_job_system_prompt_omits_mention_instructions_when_alone_in_group`, que verifica que a instrução inteira some quando o agente está sozinho no grupo).

- [ ] **Step 6: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: instruct agents to mention @name only when a response is needed"
```

---

### Task 2: fechar a spec

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-mention-only-when-response-needed-design.md`

- [ ] **Step 1: Rodar a suíte de testes inteira**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 2: Atualizar o status no topo da spec**

Trocar:

```
Status: Aprovado para planejamento
```

por:

```
Status: Implementado
```

- [ ] **Step 3: Commit final**

```bash
git add docs/superpowers/specs/2026-09-20-mention-only-when-response-needed-design.md
git commit -m "docs: mark mention-only-when-response-needed spec as implemented"
```
