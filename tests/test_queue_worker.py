import json

from app.db import get_connection
from app.queue_worker import process_next_job


def _create_agent(conn, name="bob", vision_capable=0, model_name="qwen2.5-7b"):
    cur = conn.execute(
        "INSERT INTO agents (name, persona_prompt, model_name, vision_capable) VALUES (?, ?, ?, ?)",
        (name, f"Você é {name}.", model_name, vision_capable),
    )
    return cur.lastrowid


def _create_group(conn, name="investidores"):
    cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (name,))
    return cur.lastrowid


def _create_conversation(conn, group_id, name="Geral"):
    cur = conn.execute("INSERT INTO conversations (group_id, name) VALUES (?, ?)", (group_id, name))
    return cur.lastrowid


def _add_member(conn, group_id, agent_id):
    conn.execute("INSERT INTO group_members (group_id, agent_id) VALUES (?, ?)", (group_id, agent_id))


def test_process_next_job_picks_highest_priority_first(db, monkeypatch, tmp_path):
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"fake-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'describe_image', 0, ?)",
        (conversation_id, json.dumps({"image_path": str(image_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "descrição gerada",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None


def test_process_next_job_agent_turn_posts_reply_and_marks_done(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "olá, tudo bem?")

    process_next_job()

    conn = get_connection()
    try:
        messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert len(messages) == 1
    assert messages[0]["content"] == "olá, tudo bem?"
    assert messages[0]["sender_id"] == agent_id
    assert job["status"] == "done"


def test_process_next_job_reply_with_new_mention_enqueues_follow_up(db, monkeypatch):
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

    monkeypatch.setattr(
        "app.queue_worker.chat_completion", lambda **kwargs: "@alice o que acha?"
    )

    process_next_job()

    conn = get_connection()
    try:
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE status = 'pending'"
        ).fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["agent_id"] == alice_id


def test_process_next_job_rolls_back_partial_work_on_error(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "olá, tudo bem?")

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.queue_worker.enqueue_mentions", _boom)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        system_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert agent_messages == []
    assert job["status"] == "error"
    assert len(system_messages) == 1
    assert "boom" in system_messages[0]["content"]


def test_process_next_job_agent_turn_passes_image_to_vision_capable_agent(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None


def test_process_next_job_agent_turn_no_image_for_non_vision_agent(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=0)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "texto apenas",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0].get("image_base64") is None


def test_process_next_job_vision_agent_history_excludes_hidden_description(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (conversation_id, "descrição oculta gerada pelo describe_image"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None
    contents = [m["content"] for m in calls[0]["messages"]]
    assert not any("descrição oculta gerada pelo describe_image" in c for c in contents)


def test_process_next_job_vision_agent_history_includes_hidden_message_without_kind(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (conversation_id, "mensagem oculta sem hidden_kind"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("mensagem oculta sem hidden_kind" in c for c in contents)


def test_process_next_job_non_vision_agent_history_includes_hidden_description(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=0)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (conversation_id, "descrição oculta gerada pelo describe_image"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "texto apenas",
    )

    process_next_job()

    assert len(calls) == 1
    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("descrição oculta gerada pelo describe_image" in c for c in contents)


def test_process_next_job_no_pending_jobs_is_noop(db):
    assert process_next_job() is False


def test_process_next_job_respects_loop_limit(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    for _ in range(20):
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation_id, agent_id),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.queue_worker.chat_completion", lambda **kwargs: "@bob de novo?"
    )

    process_next_job()

    conn = get_connection()
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE status = 'pending'"
        ).fetchone()["c"]
        warning = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system'"
        ).fetchone()
    finally:
        conn.close()
    assert pending_count == 19
    assert warning is not None


def test_process_next_job_queue_limit_is_scoped_per_conversation(db, monkeypatch):
    """max_pending_per_group is counted per CONVERSATION, not per group (see Task 5 of
    docs/superpowers/plans/2026-09-18-multiple-conversations.md). Saturate conversation_a's
    limit with 20 active jobs, then prove that conversation_b (same group, well under its own
    limit) still gets its follow-up mention enqueued. Under the old per-group behavior the
    group's total active count would already be 21 (20 from A + 1 from B), which is over the
    limit of 20, so B's follow-up would NOT be enqueued.
    """
    conn = get_connection()
    agent_id = _create_agent(conn)
    # A second, distinct agent to mention in the reply: an agent mentioning itself is now
    # deliberately excluded from enqueue_mentions (self-mention infinite-loop guard, see
    # app.routers.messages.enqueue_mentions), so "@bob" replying with "@bob" would no longer
    # enqueue a follow-up job — irrelevant to what this test is actually checking (that the
    # queue limit is scoped per conversation, not per group).
    other_agent_id = _create_agent(conn, name="alice")
    group_id = _create_group(conn)
    conversation_a = _create_conversation(conn, group_id, name="Conversa A")
    conversation_b = _create_conversation(conn, group_id, name="Conversa B")
    _add_member(conn, group_id, agent_id)
    _add_member(conn, group_id, other_agent_id)

    # Saturate conversation_a's own queue limit (20 active jobs), with lower priority so they
    # are not picked first by process_next_job().
    for _ in range(20):
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation_a, agent_id),
        )

    # conversation_b has a single pending job (higher priority, i.e. lower number, so it is
    # processed first and deterministically).
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_b,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 0, '{}')",
        (conversation_b, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.queue_worker.chat_completion", lambda **kwargs: "@alice de novo?"
    )

    process_next_job()

    conn = get_connection()
    try:
        pending_b = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND status = 'pending'",
            (conversation_b,),
        ).fetchone()["c"]
        warning = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'system'",
            (conversation_b,),
        ).fetchone()
    finally:
        conn.close()

    # A NEW follow-up job was enqueued for conversation_b because its own active count (1,
    # well below the limit of 20) is what gets checked — conversation_a's 20 saturated jobs
    # do not count against conversation_b's budget.
    assert pending_b == 1
    assert warning is None


def test_process_next_job_agent_searches_once_then_answers(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que dia é hoje?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["BUSCAR: data de hoje", "Hoje é 18 de setembro de 2026."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))
    monkeypatch.setattr("app.queue_worker.web_search", lambda query, **kwargs: "Hoje é 18/09/2026.")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'search_result'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Hoje é 18 de setembro de 2026."
    assert len(hidden_messages) == 1
    assert "data de hoje" in hidden_messages[0]["content"]
    assert "18/09/2026" in hidden_messages[0]["content"]


def test_process_next_job_agent_hits_search_limit_and_is_forced_to_answer(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'pesquise sem parar')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(
        [
            "BUSCAR: um",
            "BUSCAR: dois",
            "BUSCAR: tres",
            "BUSCAR: quatro",
            "Não encontrei nada definitivo, mas aqui está o que sei.",
        ]
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))
    monkeypatch.setattr("app.queue_worker.web_search", lambda query, **kwargs: f"resultado de {query}")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'search_result'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Não encontrei nada definitivo, mas aqui está o que sei."
    assert len(hidden_messages) == 3


def test_process_next_job_search_failure_does_not_crash_the_job(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["BUSCAR: algo", "Sem internet, mas posso ajudar de outra forma."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))

    def _raise(query, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.queue_worker.web_search", _raise)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "done"
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Sem internet, mas posso ajudar de outra forma."


def test_process_next_job_forced_answer_still_searching_falls_back_to_generic_message(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'pesquise sem parar')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(
        [
            "BUSCAR: um",
            "BUSCAR: dois",
            "BUSCAR: tres",
            "BUSCAR: quatro",
            "BUSCAR: mais uma vez",
        ]
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))
    monkeypatch.setattr("app.queue_worker.web_search", lambda query, **kwargs: f"resultado de {query}")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == (
        "Não consegui concluir a busca a tempo, mas posso ajudar com o que já sei — pode perguntar de novo."
    )


def test_process_next_job_detects_buscar_even_with_prose_around_it(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que dia é hoje?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["Vou pesquisar isso.\nBUSCAR: data de hoje", "Hoje é 18 de setembro de 2026."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))

    captured_queries = []

    def _fake_web_search(query, **kwargs):
        captured_queries.append(query)
        return "Hoje é 18/09/2026."

    monkeypatch.setattr("app.queue_worker.web_search", _fake_web_search)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert captured_queries == ["data de hoje"]
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Hoje é 18 de setembro de 2026."


def test_process_next_job_does_not_treat_buscar_mention_mid_sentence_as_search_command(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'o que é BUSCAR?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = "Aqui está minha resposta final sobre BUSCAR: como conceito de programação."
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    def _fail_if_called(query, **kwargs):
        raise AssertionError("web_search não deveria ser chamado para uma menção no meio da frase")

    monkeypatch.setattr("app.queue_worker.web_search", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == reply_text


def test_process_next_job_system_prompt_lists_other_group_agents_for_mentioning(db, monkeypatch):
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

    # Índice 1: a lista de membros do grupo (roster) e as instruções de menção vivem numa
    # mensagem de sistema própria, separada da persona (mensagem 0) — ver _build_history.
    system_content = calls[0]["messages"][1]["content"]
    assert "@alice" in system_content
    assert "mencionar outros agentes" in system_content


def test_process_next_job_system_prompt_omits_mention_instructions_when_alone_in_group(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
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
    assert "mencionar outros agentes" not in system_content


def test_process_next_job_buscar_same_line_preamble_leaks_as_text_not_search(db, monkeypatch):
    """Documents the accepted trade-off of the line-anchored SEARCH_PATTERN: a preamble on the
    SAME line as `BUSCAR:` (e.g. "Vou pesquisar. BUSCAR: x") is deliberately NOT detected as a
    search command, so it leaks through as plain text instead of triggering a search. This is
    the accepted risk (see the comment above SEARCH_PATTERN in app/queue_worker.py) — a smaller
    cost than the false positive of matching "BUSCAR:" anywhere in the response, which fires
    unwanted network calls. Do not "fix" this by switching back to an unanchored `.search()`.
    """
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que tempo faz em SP?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = "Vou pesquisar. BUSCAR: clima em SP"
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    def _fail_if_called(query, **kwargs):
        raise AssertionError("web_search não deveria ser chamado quando o preâmbulo está na mesma linha")

    monkeypatch.setattr("app.queue_worker.web_search", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == reply_text


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


def test_build_history_truncates_and_adds_system_note(db):
    from app.queue_worker import _build_history

    conn = get_connection()
    agent_id = _create_agent(conn, name="carlos")
    group_id = _create_group(conn, name="grupo_historico")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Longa")
    _add_member(conn, group_id, agent_id)

    # Inserir 25 mensagens
    for i in range(25):
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', ?)",
            (conversation_id, f"Mensagem {i + 1}"),
        )
    conn.commit()

    # Com max_messages=10, deve manter a persona, a nota de 15 mensagens omitidas e as 10 últimas mensagens
    history = _build_history(
        conn,
        conversation_id,
        "Você é um assistente.",
        agent_id,
        "carlos",
        max_messages=10,
    )
    conn.close()

    assert history[0]["role"] == "system"
    assert "Você é um assistente." in history[0]["content"]

    assert history[1]["role"] == "system"
    assert "15 mensagens" in history[1]["content"]
    assert "condensado/omitido" in history[1]["content"]

    # Deve ter 10 mensagens de usuário subsequentes (de 16 a 25)
    user_msgs = [m for m in history if m["role"] == "user"]
    assert len(user_msgs) == 10
    assert user_msgs[0]["content"] == "Mensagem 16"
    assert user_msgs[-1]["content"] == "Mensagem 25"


def test_process_agent_turn_retries_with_reduced_history_on_context_exceeded(db, monkeypatch):
    import httpx

    conn = get_connection()
    agent_id = _create_agent(conn, name="debora")
    group_id = _create_group(conn, name="grupo_fallback")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Fallback")
    _add_member(conn, group_id, agent_id)

    for i in range(30):
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', ?)",
            (conversation_id, f"Mensagem {i + 1}"),
        )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    call_args = []

    def mock_chat_completion(**kwargs):
        call_args.append(kwargs)
        if len(call_args) == 1:
            req = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
            resp = httpx.Response(400, request=req)
            raise httpx.HTTPStatusError("llama-swap (400): exceeds the available context size", request=req, response=resp)
        return "Resposta após recuperação do contexto."

    monkeypatch.setattr("app.queue_worker.chat_completion", mock_chat_completion)

    assert process_next_job() is True

    # Confirmar que tentou novamente com histórico reduzido
    assert len(call_args) == 2
    # A segunda chamada deve ter mensagens reduzidas (max_messages=15)
    assert len(call_args[1]["messages"]) < len(call_args[0]["messages"])

    conn = get_connection()
    try:
        reply_msg = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'agent'",
            (conversation_id,),
        ).fetchone()
        assert reply_msg is not None
        assert reply_msg["content"] == "Resposta após recuperação do contexto."
    finally:
        conn.close()


def test_process_agent_turn_wait_user_tag_cancels_pending_jobs_and_suppresses_mentions(db, monkeypatch):
    conn = get_connection()
    ana_id = _create_agent(conn, name="ana")
    mansur_id = _create_agent(conn, name="mansur")
    milton_id = _create_agent(conn, name="milton")
    group_id = _create_group(conn, name="comite")
    conversation_id = _create_conversation(conn, group_id, name="Finanças")
    _add_member(conn, group_id, ana_id)
    _add_member(conn, group_id, mansur_id)
    _add_member(conn, group_id, milton_id)

    # Usuário chamou @all e enfileirou todos os 3
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@all como estão minhas contas?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, ana_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, mansur_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, milton_id),
    )
    conn.commit()
    conn.close()

    # Ana responde pedindo números, incluindo [[AGUARDANDO_USUARIO]] e menção a @mansur
    reply_text = (
        "Preencha seus dados brutos:\n"
        "1. Gastos: R$ _____\n"
        "Estou aguardando seus números!\n"
        "[[AGUARDANDO_USUARIO]]\n"
        "Quando você mandar, @mansur analisará."
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    # Processa o turno da Ana
    assert process_next_job() is True

    conn = get_connection()
    try:
        # A mensagem da Ana foi salva limpa (sem [[AGUARDANDO_USUARIO]]) e com hidden_kind='wait_user'
        ana_msg = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'agent'",
            (conversation_id,),
        ).fetchone()
        assert ana_msg is not None
        assert "[[AGUARDANDO_USUARIO]]" not in ana_msg["content"]
        assert "Preencha seus dados brutos" in ana_msg["content"]
        assert ana_msg["hidden_kind"] == "wait_user"

        # O job da Ana foi concluído ('done')
        ana_job = conn.execute("SELECT status FROM queue_jobs WHERE agent_id = ?", (ana_id,)).fetchone()
        assert ana_job["status"] == "done"

        # Os jobs pendentes de Mansur e Milton foram cancelados ('error') para poupar contexto!
        mansur_job = conn.execute("SELECT status FROM queue_jobs WHERE agent_id = ?", (mansur_id,)).fetchone()
        milton_job = conn.execute("SELECT status FROM queue_jobs WHERE agent_id = ?", (milton_id,)).fetchone()
        assert mansur_job["status"] == "error"
        assert milton_job["status"] == "error"

        # Nenhuma nova menção a @mansur foi enfileirada (total de jobs = 3)
        total_jobs = conn.execute("SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ?", (conversation_id,)).fetchone()["c"]
        assert total_jobs == 3
    finally:
        conn.close()

    # Próximo process_next_job não encontra nenhum job pending! Fila parou!
    assert process_next_job() is False


def test_process_agent_turn_wait_user_heuristic_cancels_pending_jobs(db, monkeypatch):
    conn = get_connection()
    ana_id = _create_agent(conn, name="ana_h")
    mansur_id = _create_agent(conn, name="mansur_h")
    group_id = _create_group(conn, name="comite_h")
    conversation_id = _create_conversation(conn, group_id, name="Finanças H")
    _add_member(conn, group_id, ana_id)
    _add_member(conn, group_id, mansur_id)

    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, ana_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, mansur_id),
    )
    conn.commit()
    conn.close()

    # Sem a tag explícita, mas contendo formulário com 'R$ _____'
    reply_text = "Por favor informe seus gastos: 1. iFood: R$ _____ 2. Uber: R$ _____"
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    assert process_next_job() is True

    conn = get_connection()
    try:
        ana_msg = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'agent'",
            (conversation_id,),
        ).fetchone()
        assert ana_msg["hidden_kind"] == "wait_user"

        mansur_job = conn.execute("SELECT status FROM queue_jobs WHERE agent_id = ?", (mansur_id,)).fetchone()
        assert mansur_job["status"] == "error"
    finally:
        conn.close()


def test_process_agent_turn_prompt_includes_wait_user_and_anti_repeat_instructions(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn, name="leo_prompt")
    group_id = _create_group(conn, name="grupo_prompt")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Prompt")
    _add_member(conn, group_id, agent_id)

    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    captured_messages = []

    def mock_chat(**kwargs):
        captured_messages.extend(kwargs["messages"])
        return "Tudo ok!"

    monkeypatch.setattr("app.queue_worker.chat_completion", mock_chat)

    process_next_job()

    system_msg = captured_messages[0]["content"]
    assert "[[AGUARDANDO_USUARIO]]" in system_msg
    assert "NUNCA repita as perguntas" in system_msg
    assert "NÃO responda apenas para dizer que está aguardando" in system_msg


def test_process_next_job_extract_pdf_saves_hidden_message_and_marks_done(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"fake-pdf-bytes")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.extract_text", lambda path, **kwargs: "texto extraído do pdf")

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "texto extraído do pdf"
    assert job["status"] == "done"


def test_process_next_job_extract_pdf_with_no_text_saves_warning_message(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"fake-pdf-bytes")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.extract_text", lambda path, **kwargs: "")

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
    finally:
        conn.close()

    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "Nenhum texto extraível encontrado neste PDF (pode ser um documento escaneado)."


def test_process_next_job_extract_pdf_failure_marks_job_error(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"not a pdf")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    def _raise(path, **kwargs):
        raise RuntimeError("pdf inválido")

    monkeypatch.setattr("app.queue_worker.extract_text", _raise)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
        system_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system' AND hidden = 0"
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "error"
    assert len(system_messages) == 1
    assert "pdf inválido" in system_messages[0]["content"]


def test_process_next_job_agent_turn_history_includes_pdf_extract_text(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob olha esse pdf')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'pdf_extract')",
        (conversation_id, "conteúdo extraído do pdf de teste"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "vi o pdf",
    )

    process_next_job()

    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("conteúdo extraído do pdf de teste" in c for c in contents)


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

    system_content = calls[0]["messages"][1]["content"]
    assert "SOMENTE quando" in system_content
    assert "SEM o @" in system_content
    assert "@Ana, pode confirmar esse número" in system_content
    assert "Concordo com o que a Ana falou" in system_content


def test_process_agent_turn_heuristic_detects_numbered_option_choice(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn, name="ana_opts")
    group_id = _create_group(conn, name="grupo_opts")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Opts")
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = (
        "Escolha uma dessas opções: 1. Segurança 2. Eficiência 3. Híbrida. Qual você escolhe?"
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    process_next_job()

    conn = get_connection()
    try:
        agent_msg = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'agent'",
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    assert agent_msg["hidden_kind"] == "wait_user"


def test_process_agent_turn_skips_llm_call_when_conversation_awaiting_user(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn, name="milton")
    group_id = _create_group(conn, name="grupo_espera")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Espera")
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
        "VALUES (?, 'agent', ?, 'Escolha 1, 2 ou 3.', 'wait_user')",
        (conversation_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    def _fail_if_called(**kwargs):
        raise AssertionError("chat_completion não deveria ser chamado")

    monkeypatch.setattr("app.queue_worker.chat_completion", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "done"
    assert len(agent_messages) == 1  # só a mensagem wait_user original, nenhuma nova


def test_process_agent_turn_runs_normally_after_user_replies(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn, name="milton2")
    group_id = _create_group(conn, name="grupo_espera2")
    conversation_id = _create_conversation(conn, group_id, name="Conversa Espera 2")
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
        "VALUES (?, 'agent', ?, 'Escolha 1, 2 ou 3.', 'wait_user')",
        (conversation_id, agent_id),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'Escolho a opção 2')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "Perfeito, seguindo com a opção 2.")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()
    assert len(agent_messages) == 2
    assert agent_messages[1]["content"] == "Perfeito, seguindo com a opção 2."


def test_process_next_job_extract_pdf_runs_even_when_conversation_awaiting_user(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"fake-pdf-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, name="waiting_agent")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
        "VALUES (?, 'agent', ?, 'Escolha 1, 2 ou 3.', 'wait_user')",
        (conversation_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.extract_text", lambda path, **kwargs: "texto do pdf")

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
    finally:
        conn.close()
    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "texto do pdf"


def test_process_agent_turn_heuristic_ignores_rhetorical_qual_sera(db, monkeypatch):
    """'Qual será o resultado disso?' é uma pergunta retórica comum entre agentes discutindo
    entre si, não um pedido de decisão dirigido ao usuário — não deve pausar a fila."""
    conn = get_connection()
    agent_id = _create_agent(conn, name="analista")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = "Qual será o impacto disso no orçamento até o fim do trimestre?"
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    process_next_job()

    conn = get_connection()
    try:
        agent_msg = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND sender_type = 'agent'",
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    assert agent_msg["hidden_kind"] is None


def test_process_agent_turn_circuit_breaker_blocks_stray_future_mention(db, monkeypatch):
    """Cenário real que motivou a correção: um agente escreve uma menção condicional/futura
    (ex.: "quando você decidir, @Ana vai analisar") depois que outro agente já pediu a decisão
    do usuário. A menção cria um novo job pra Ana, mas a trava mecânica deve bloqueá-lo sem
    chamar o modelo, porque a conversa continua "aguardando usuário"."""
    conn = get_connection()
    ana_id = _create_agent(conn, name="ana")
    milton_id = _create_agent(conn, name="milton")
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, ana_id)
    _add_member(conn, group_id, milton_id)

    # Milton já pediu a decisão do usuário (mensagem mais recente visível, hidden_kind='wait_user')
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
        "VALUES (?, 'agent', ?, 'Escolha 1, 2 ou 3. Quando decidir, @ana entrará em ação.', 'wait_user')",
        (conversation_id, milton_id),
    )
    # A própria menção "@ana" acima teria disparado este job (fora do escopo deste teste simular
    # o enqueue_mentions; aqui só confirmamos que, uma vez criado, a trava o bloqueia).
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, ana_id),
    )
    conn.commit()
    conn.close()

    def _fail_if_called(**kwargs):
        raise AssertionError("chat_completion não deveria ser chamado")

    monkeypatch.setattr("app.queue_worker.chat_completion", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs WHERE agent_id = ?", (ana_id,)).fetchone()
        ana_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent' AND sender_id = ?", (ana_id,)
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "done"
    assert ana_messages == []


def test_deleting_hidden_message_removes_it_from_agent_context(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob olha o pdf')",
        (conversation_id,),
    )
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'pdf_extract')",
        (conversation_id, "conteudo sensivel que deve sumir"),
    )
    pdf_message_id = cur.lastrowid
    conn.commit()

    # Simula o usuário apagando a mensagem oculta antes do agente responder
    conn.execute("DELETE FROM messages WHERE id = ?", (pdf_message_id,))
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "ok",
    )

    process_next_job()

    contents = [m["content"] for m in calls[0]["messages"]]
    assert not any("conteudo sensivel que deve sumir" in c for c in contents)
