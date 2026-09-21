from app.db import get_connection
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.messages import enqueue_mentions


def make_client(db):
    return TestClient(create_app())


def _create_group(client, name="investidores"):
    return client.post("/api/groups", json={"name": name}).json()


def test_list_conversations_includes_default_geral(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]


def test_create_conversation(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "Due diligence"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Due diligence"
    assert body["group_id"] == group["id"]

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert sorted(c["name"] for c in resp.json()) == ["Due diligence", "Geral"]


def test_create_conversation_rejects_empty_name(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "   "})
    assert resp.status_code == 400


def test_create_conversation_404_for_unknown_group(db):
    client = make_client(db)
    resp = client.post("/api/groups/9999/conversations", json={"name": "x"})
    assert resp.status_code == 404


def test_rename_conversation(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}",
        json={"name": "Renomeada"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renomeada"


def test_rename_conversation_404_for_unknown_id(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.put(f"/api/groups/{group['id']}/conversations/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_non_last_conversation_leaves_the_rest(db):
    client = make_client(db)
    group = _create_group(client)
    geral = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    extra = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "extra"}).json()

    resp = client.delete(f"/api/groups/{group['id']}/conversations/{extra['id']}")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]
    assert resp.json()[0]["id"] == geral["id"]


def test_delete_last_conversation_recreates_geral(db):
    client = make_client(db)
    group = _create_group(client)
    geral = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    resp = client.delete(f"/api/groups/{group['id']}/conversations/{geral['id']}")
    assert resp.status_code == 200
    remaining = resp.json()
    assert [c["name"] for c in remaining] == ["Geral"]
    assert remaining[0]["id"] != geral["id"]  # é uma conversa nova, recriada


def test_delete_conversation_404_for_unknown_id(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.delete(f"/api/groups/{group['id']}/conversations/9999")
    assert resp.status_code == 404


def test_stop_cancels_pending_jobs_and_leaves_processing(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('leo', 'p', 'm')"
        )
        agent_id = cur.lastrowid
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation["id"], agent_id),
        )
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload, status) "
            "VALUES (?, ?, 'agent_turn', 1, '{}', 'processing')",
            (conversation["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.post(f"/api/groups/{group['id']}/conversations/{conversation['id']}/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"cancelled_pending": 1, "still_processing": 1}

    conn = get_connection()
    try:
        statuses = {
            row["status"]
            for row in conn.execute(
                "SELECT status FROM queue_jobs WHERE conversation_id = ?", (conversation["id"],)
            )
        }
        system_message = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND sender_type = 'system'",
            (conversation["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert statuses == {"error", "processing"}
    assert "1 resposta" in system_message["content"]


def test_stop_blocks_mention_enqueued_by_reply_already_in_flight(db):
    """Reproduces the race the user hit: a job that was 'processing' when stop was clicked
    finishes afterwards and its reply @mentions another agent. Before the stopped_at flag,
    enqueue_mentions had no way to know stop had been requested and created a fresh job
    anyway — letting the mention chain outlive the cancellation. It must now be a no-op."""
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    conn = get_connection()
    try:
        leo_id = conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('leo', 'p', 'm')"
        ).lastrowid
        ana_id = conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('ana', 'p', 'm')"
        ).lastrowid
        conn.execute(
            "INSERT INTO group_members (group_id, agent_id) VALUES (?, ?), (?, ?)",
            (group["id"], leo_id, group["id"], ana_id),
        )
        trigger_id = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'agent', ?, 'ainda processando...')",
            (conversation["id"], leo_id),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    resp = client.post(f"/api/groups/{group['id']}/conversations/{conversation['id']}/stop")
    assert resp.status_code == 200

    # Simula o que queue_worker._process_agent_turn faz ao terminar: insere a resposta do job
    # que já estava em andamento e tenta encadear a menção que ela contém.
    conn = get_connection()
    try:
        enqueue_mentions(
            conn, conversation["id"], trigger_id, "@ana pode confirmar isso?", author_agent_id=leo_id
        )
        conn.commit()
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND status = 'pending'",
            (conversation["id"],),
        ).fetchone()["c"]
    finally:
        conn.close()
    assert pending == 0


def test_new_user_message_resumes_mentions_after_stop(db):
    """A stopped conversation isn't stopped forever: sending a new message is the user's
    signal to continue, so it must clear stopped_at and let @mentions enqueue jobs again."""
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    conn = get_connection()
    try:
        ana_id = conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('ana', 'p', 'm')"
        ).lastrowid
        conn.execute(
            "INSERT INTO group_members (group_id, agent_id) VALUES (?, ?)", (group["id"], ana_id)
        )
        conn.commit()
    finally:
        conn.close()

    client.post(f"/api/groups/{group['id']}/conversations/{conversation['id']}/stop")

    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "@ana oi de novo"}
    )
    assert resp.status_code == 201

    conn = get_connection()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND agent_id = ? "
            "AND status = 'pending'",
            (conversation["id"], ana_id),
        ).fetchone()["c"]
    finally:
        conn.close()
    assert pending == 1


def test_stop_404_for_unknown_conversation(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations/9999/stop")
    assert resp.status_code == 404


def _setup_two_agents(client, group):
    leo = client.post(
        "/api/agents", json={"name": "leo", "persona_prompt": "p", "model_name": "m", "vision_capable": False}
    ).json()
    ana = client.post(
        "/api/agents", json={"name": "ana", "persona_prompt": "p", "model_name": "m", "vision_capable": False}
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": leo["id"]})
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": ana["id"]})
    return leo, ana


def test_agent_mention_setting_starts_unset(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    _setup_two_agents(client, group)

    resp = client.get(f"/api/groups/{group['id']}/conversations/{conversation['id']}/agent-mention-settings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_set_agent_mention_setting_blocks_agent_authored_mention_but_not_human(db):
    """The core behavior: with human_only_mention=1 for 'ana', an agent's own reply
    @mentioning her must not enqueue a job, but a human-authored message @mentioning her
    still works."""
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    leo, ana = _setup_two_agents(client, group)

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}/agent-mention-settings/{ana['id']}",
        json={"human_only_mention": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"agent_id": ana["id"], "human_only_mention": True}

    conn = get_connection()
    try:
        trigger_id = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'agent', ?, 'oi @ana')",
            (conversation["id"], leo["id"]),
        ).lastrowid
        conn.commit()
        enqueue_mentions(conn, conversation["id"], trigger_id, "oi @ana", author_agent_id=leo["id"])
        conn.commit()
        agent_authored_jobs = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND agent_id = ?",
            (conversation["id"], ana["id"]),
        ).fetchone()["c"]
    finally:
        conn.close()
    assert agent_authored_jobs == 0

    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@ana oi"})
    assert resp.status_code == 201
    conn = get_connection()
    try:
        human_authored_jobs = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND agent_id = ?",
            (conversation["id"], ana["id"]),
        ).fetchone()["c"]
    finally:
        conn.close()
    assert human_authored_jobs == 1


def test_set_agent_mention_setting_404_for_agent_not_in_group(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    outsider = client.post(
        "/api/agents", json={"name": "fora", "persona_prompt": "p", "model_name": "m", "vision_capable": False}
    ).json()

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}/agent-mention-settings/{outsider['id']}",
        json={"human_only_mention": True},
    )
    assert resp.status_code == 404


def test_select_all_shortcut_sets_every_member_then_clears_them(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    leo, ana = _setup_two_agents(client, group)

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}/agent-mention-settings",
        json={"human_only_mention": True},
    )
    assert resp.status_code == 200
    assert {row["agent_id"]: row["human_only_mention"] for row in resp.json()} == {
        leo["id"]: True,
        ana["id"]: True,
    }

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}/agent-mention-settings",
        json={"human_only_mention": False},
    )
    assert resp.status_code == 200
    assert {row["agent_id"]: row["human_only_mention"] for row in resp.json()} == {
        leo["id"]: False,
        ana["id"]: False,
    }
