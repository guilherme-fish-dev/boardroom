import json

from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import create_app
from app.queue_worker import process_next_job
from app.routers.messages import _pair_exchange_count
from test_pdf_extract import _minimal_pdf_bytes


def make_client(db):
    return TestClient(create_app())


def _setup_group_with_agent(client, agent_name="bob"):
    agent = client.post(
        "/api/agents",
        json={
            "name": agent_name,
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    return group, agent, conversation


def test_messages_are_isolated_between_conversations_in_the_same_group(db):
    client = make_client(db)
    group, agent, geral = _setup_group_with_agent(client)
    outra = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "outra"}).json()

    client.post(f"/api/conversations/{geral['id']}/messages", json={"content": "mensagem na geral"})
    client.post(f"/api/conversations/{outra['id']}/messages", json={"content": "mensagem na outra"})

    geral_contents = [m["content"] for m in client.get(f"/api/conversations/{geral['id']}/messages").json()]
    outra_contents = [m["content"] for m in client.get(f"/api/conversations/{outra['id']}/messages").json()]

    assert geral_contents == ["mensagem na geral"]
    assert outra_contents == ["mensagem na outra"]


def test_post_message_without_mention_creates_no_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "oi pessoal"})
    assert resp.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_post_message_with_mention_creates_agent_turn_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@bob o que acha?"})
    message = resp.json()

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "agent_turn"
    assert jobs[0]["agent_id"] == agent["id"]
    assert jobs[0]["priority"] == 1
    assert jobs[0]["conversation_id"] == conversation["id"]
    payload = json.loads(jobs[0]["payload"])
    assert payload["trigger_message_id"] == message["id"]


def test_post_message_with_all_mention_enqueues_every_group_member_once(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "@all @bob, opinem"}
    )
    assert response.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT agent_id FROM queue_jobs ORDER BY id").fetchall()
    finally:
        conn.close()

    assert [job["agent_id"] for job in jobs] == [bob["id"], alice["id"]]


def test_post_message_mentioning_non_member_creates_no_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@alguem-que-nao-existe oi"})

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_post_message_404_for_unknown_conversation(db):
    client = make_client(db)
    resp = client.post("/api/conversations/9999/messages", json={"content": "oi"})
    assert resp.status_code == 404


def test_post_message_with_multiple_mentions_enqueues_jobs_in_text_order(db):
    client = make_client(db)
    group, bob, conversation = _setup_group_with_agent(client, agent_name="bob")
    carla = client.post(
        "/api/agents",
        json={"name": "carla", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    alice = client.post(
        "/api/agents",
        json={"name": "alice", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": carla["id"]})
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": alice["id"]})

    # Mentioned out of DB-insertion order: carla, alice, bob — jobs must follow this text order.
    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@carla @alice @bob e ai?"})

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT agent_id FROM queue_jobs ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [j["agent_id"] for j in jobs] == [carla["id"], alice["id"], bob["id"]]


def test_list_messages_excludes_hidden(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "visível"})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
            (conversation["id"], "oculta"),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages")
    contents = [m["content"] for m in resp.json()]
    assert contents == ["visível"]


def test_list_messages_since_id_returns_only_newer(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    first = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "primeira"}).json()
    second = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "segunda"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages", params={"since_id": first["id"]})
    contents = [m["content"] for m in resp.json()]
    assert contents == ["segunda"]


def test_upload_image_creates_message_and_priority_job(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "olha essa foto"},
        files=files,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["image_path"] is not None
    assert body["conversation_id"] == conversation["id"]

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "describe_image"
    assert jobs[0]["priority"] == 0
    assert jobs[0]["conversation_id"] == conversation["id"]


def test_upload_image_rejects_non_image_content_type(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("notes.txt", b"just text", "text/plain")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "isso não é imagem"},
        files=files,
    )
    assert resp.status_code == 415


def test_upload_image_rejects_oversized_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    big_payload = b"x" * (10 * 1024 * 1024 + 1)
    files = {"image": ("big.png", big_payload, "image/png")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "arquivo grande"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_image_404_for_unknown_conversation(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    resp = client.post(
        "/api/conversations/9999/messages/image",
        data={"content": "foto"},
        files=files,
    )
    assert resp.status_code == 404


def test_get_message_image_returns_file_bytes(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "foto"},
        files=files,
    ).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/image")
    assert resp.status_code == 200
    assert resp.content == b"fake-png-bytes"


def test_get_message_image_404_when_no_image(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "sem imagem"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/image")
    assert resp.status_code == 404


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


def test_user_reply_without_mention_auto_enqueues_waiting_agent(db):
    client = make_client(db)
    group, ana, conversation = _setup_group_with_agent(client, agent_name="ana")

    # Simula que o agente Ana postou uma pergunta com hidden_kind='wait_user'
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
            "VALUES (?, 'agent', ?, 'Preencha seus 3 gastos: R$ _____', 'wait_user')",
            (conversation["id"], ana["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # O usuário envia uma mensagem SEM menção @
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "1. R$ 500, 2. R$ 200, 3. R$ 100"},
    )
    assert resp.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs WHERE conversation_id = ?", (conversation["id"],)).fetchall()
        assert len(jobs) == 1
        assert jobs[0]["agent_id"] == ana["id"]
        assert jobs[0]["job_type"] == "agent_turn"
        assert jobs[0]["status"] == "pending"
    finally:
        conn.close()


def test_user_reply_with_explicit_mention_overrides_waiting_agent(db):
    client = make_client(db)
    group, ana, conversation = _setup_group_with_agent(client, agent_name="ana")
    mansur = client.post(
        "/api/agents",
        json={"name": "mansur", "persona_prompt": "x", "model_name": "qwen2.5-7b", "vision_capable": False},
    ).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": mansur["id"]})

    # Simula que Ana estava aguardando o usuário
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
            "VALUES (?, 'agent', ?, 'Preencha seus 3 gastos: R$ _____', 'wait_user')",
            (conversation["id"], ana["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # O usuário responde mencionando explicitamente @mansur
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "@mansur veja esses gastos"},
    )
    assert resp.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs WHERE conversation_id = ?", (conversation["id"],)).fetchall()
        # Apenas @mansur foi enfileirado porque o usuário expressou menção explícita
        assert len(jobs) == 1
        assert jobs[0]["agent_id"] == mansur["id"]
    finally:
        conn.close()


def test_upload_pdf_creates_message_and_priority_job(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "segue o relatório"},
        files=files,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["pdf_path"] is not None
    assert body["conversation_id"] == conversation["id"]

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "extract_pdf"
    assert jobs[0]["priority"] == 0
    assert jobs[0]["conversation_id"] == conversation["id"]


def test_upload_pdf_rejects_non_pdf_content_type(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("notes.txt", b"just text", "text/plain")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "isso não é pdf"},
        files=files,
    )
    assert resp.status_code == 415


def test_upload_pdf_rejects_oversized_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    big_payload = b"x" * (10 * 1024 * 1024 + 1)
    files = {"pdf": ("big.pdf", big_payload, "application/pdf")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "arquivo grande"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_pdf_404_for_unknown_conversation(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    resp = client.post(
        "/api/conversations/9999/messages/pdf",
        data={"content": "relatório"},
        files=files,
    )
    assert resp.status_code == 404


def test_get_message_pdf_returns_file_bytes(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "relatório"},
        files=files,
    ).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/pdf")
    assert resp.status_code == 200
    assert resp.content == b"fake-pdf-bytes"


def test_get_message_pdf_404_when_no_pdf(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "sem pdf"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/pdf")
    assert resp.status_code == 404


def test_uploaded_pdf_is_extracted_end_to_end_without_mocking(db, tmp_path, monkeypatch):
    """Fim a fim, sem mockar extract_text: sobe um PDF real (gerado com o mesmo helper de
    tests/test_pdf_extract.py), deixa o job extract_pdf rodar de verdade contra o arquivo
    salvo em disco, e confirma que o texto extraído aparece como mensagem oculta. Os outros
    testes de endpoint e de worker mockam extract_text ou o pdf_path — este é o único que
    exercita a integração real entre upload, caminho do arquivo em disco, e extração."""
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    pdf_bytes = _minimal_pdf_bytes(["Relatório trimestral", "Receita subiu 12%"])
    files = {"pdf": ("relatorio.pdf", pdf_bytes, "application/pdf")}
    client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "segue o relatório"},
        files=files,
    )

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
    finally:
        conn.close()

    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "Relatório trimestral\nReceita subiu 12%"
