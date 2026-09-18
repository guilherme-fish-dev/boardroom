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


def _add_member(conn, group_id, agent_id):
    conn.execute("INSERT INTO group_members (group_id, agent_id) VALUES (?, ?)", (group_id, agent_id))


def test_process_next_job_picks_highest_priority_first(db, monkeypatch, tmp_path):
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"fake-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'describe_image', 0, ?)",
        (group_id, json.dumps({"image_path": str(image_path), "message_id": 1})),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, bob_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (group_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (group_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (group_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (group_id, "descrição oculta gerada pelo describe_image"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (group_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (group_id, "mensagem oculta sem hidden_kind"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (group_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (group_id, "descrição oculta gerada pelo describe_image"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
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
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    for _ in range(20):
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (group_id, agent_id),
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
