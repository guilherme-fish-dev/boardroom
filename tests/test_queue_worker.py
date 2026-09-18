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
