import json
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_connection
from app.mentions import extract_mentions
from app.tts import is_available as tts_is_available
from app.tts import purge_stale_cache, synthesize_wav

router = APIRouter(prefix="/api/conversations/{conversation_id}/messages", tags=["messages"])


class MessageIn(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_type: str
    sender_id: int | None
    content: str
    image_path: str | None
    pdf_path: str | None
    hidden: bool
    hidden_kind: str | None
    created_at: str


class PendingJobOut(BaseModel):
    agent_id: int | None
    agent_name: str | None
    job_type: str


def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        pdf_path=row["pdf_path"],
        hidden=bool(row["hidden"]),
        hidden_kind=row["hidden_kind"],
        created_at=row["created_at"],
    )


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


def enqueue_mentions(
    conn: sqlite3.Connection,
    conversation_id: int,
    trigger_message_id: int,
    content: str,
    *,
    author_agent_id: int | None = None,
) -> None:
    """Create agent_turn jobs for every mentioned agent that is a member of the conversation's
    group. author_agent_id, when the content being scanned is itself an agent's own reply,
    excludes that agent from the jobs created — otherwise an agent that mentions its own name
    (e.g. quoting itself, or a persona prompt that has it sign its messages) would enqueue a
    turn for itself and could keep doing so forever, one job triggering the next."""
    names = extract_mentions(content)
    if not names:
        return

    conversation = conn.execute(
        "SELECT group_id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        # Defensive only: callers (post_message, post_image_message, queue_worker) always
        # pass a conversation_id they just confirmed exists.
        return

    member_rows = conn.execute(
        """
        SELECT agents.id, lower(agents.name) AS name FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        WHERE group_members.group_id = ?
        ORDER BY agents.id
        """,
        (conversation["group_id"],),
    ).fetchall()
    agent_ids_by_name = {row["name"]: row["id"] for row in member_rows}

    # Preserve the text order. At @all, expand to every member in a stable order; an
    # explicit mention of a member already expanded by @all must not create a duplicate job.
    mentioned_agent_ids: list[int] = []
    seen_agent_ids: set[int] = set()
    for name in names:
        target_ids = (
            [row["id"] for row in member_rows]
            if name == "all"
            else [agent_ids_by_name.get(name)]
        )
        for agent_id in target_ids:
            if agent_id is not None and agent_id not in seen_agent_ids:
                mentioned_agent_ids.append(agent_id)
                seen_agent_ids.add(agent_id)

    for agent_id in mentioned_agent_ids:
        if agent_id == author_agent_id:
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





# Messages are hidden=1 by default so they don't clutter the visible chat (e.g. the raw
# image description text, or the "BUSCAR: ..." search-tool exchange with the model) while
# still being fed back to the LLM as context (queue_worker._build_history doesn't filter by
# hidden at all). search_result is the one hidden kind that's useful to *show* the user (so
# they can tell a reply was actually backed by a web search, not guessed) — displayed as a
# small chip rather than a full bubble, so it stays included here despite hidden=1.
_VISIBLE_MESSAGES_WHERE = "conversation_id = ? AND (hidden = 0 OR hidden_kind = 'search_result')"


@router.get("", response_model=list[MessageOut])
def list_messages(
    conversation_id: int, since_id: int | None = None, include_hidden: bool = False
) -> list[MessageOut]:
    conn = get_connection()
    try:
        where = "conversation_id = ?" if include_hidden else _VISIBLE_MESSAGES_WHERE
        if since_id is None:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {where} ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {where} AND id > ? ORDER BY id",
                (conversation_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


@router.get("/pending", response_model=list[PendingJobOut])
def list_pending_jobs(conversation_id: int) -> list[PendingJobOut]:
    """Agent/image jobs still queued or running for this conversation, so the UI can show
    a "Fulano está respondendo..." indicator instead of leaving the user guessing."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT queue_jobs.agent_id AS agent_id, agents.name AS agent_name, queue_jobs.job_type AS job_type "
            "FROM queue_jobs LEFT JOIN agents ON agents.id = queue_jobs.agent_id "
            "WHERE queue_jobs.conversation_id = ? AND queue_jobs.status IN ('pending', 'processing') "
            "ORDER BY queue_jobs.id",
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        PendingJobOut(agent_id=r["agent_id"], agent_name=r["agent_name"], job_type=r["job_type"])
        for r in rows
    ]


def _maybe_enqueue_waiting_agent(
    conn: sqlite3.Connection, conversation_id: int, trigger_message_id: int, content: str
) -> bool:
    """If the message has explicit @mentions, enqueue those agents. Otherwise, check if
    the most recent visible message in the conversation was from an agent waiting for user
    action (hidden_kind='wait_user'). If so, automatically enqueue that agent to continue."""
    names = extract_mentions(content)
    if names:
        enqueue_mentions(conn, conversation_id, trigger_message_id, content)
        return True

    last_msg = conn.execute(
        "SELECT sender_type, sender_id, hidden_kind FROM messages "
        "WHERE conversation_id = ? AND id < ? AND hidden = 0 ORDER BY id DESC LIMIT 1",
        (conversation_id, trigger_message_id),
    ).fetchone()
    if (
        last_msg
        and last_msg["sender_type"] == "agent"
        and last_msg["hidden_kind"] == "wait_user"
        and last_msg["sender_id"] is not None
    ):
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (conversation_id, last_msg["sender_id"], json.dumps({"trigger_message_id": trigger_message_id})),
        )
        return True
    return False


@router.post("", response_model=MessageOut, status_code=201)
def post_message(conversation_id: int, message: MessageIn) -> MessageOut:
    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'user', NULL, ?)",
            (conversation_id, message.content),
        )
        message_id = cur.lastrowid
        _maybe_enqueue_waiting_agent(conn, conversation_id, message_id, message.content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


def _upload_dir() -> Path:
    path = Path(os.environ.get("BOARDROOM_UPLOAD_DIR", "./data/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/image", response_model=MessageOut, status_code=201)
async def post_image_message(
    conversation_id: int, content: str = Form(""), image: UploadFile = File(...)
) -> MessageOut:
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="file must be an image")

    body = await image.read()
    if len(body) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="image too large (max 10MB)")

    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        suffix = Path(image.filename or "upload.png").suffix or ".png"
        dest = _upload_dir() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(body)

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, image_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (conversation_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'describe_image', 0, ?)",
            (conversation_id, json.dumps({"image_path": str(dest), "message_id": message_id})),
        )
        _maybe_enqueue_waiting_agent(conn, conversation_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


def _tts_cache_dir() -> Path:
    path = Path(os.environ.get("BOARDROOM_TTS_CACHE_DIR", "./data/tts_cache"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/{message_id}/audio")
def get_message_audio(conversation_id: int, message_id: int) -> Response:
    if not tts_is_available():
        raise HTTPException(status_code=503, detail="voz não configurada no servidor")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT content FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")

    cache_dir = _tts_cache_dir()
    purge_stale_cache(cache_dir)
    cache_path = cache_dir / f"{message_id}.wav"
    if not cache_path.exists():
        if not row["content"].strip():
            raise HTTPException(status_code=422, detail="mensagem sem texto para ler")
        cache_path.write_bytes(synthesize_wav(row["content"]))
    return Response(content=cache_path.read_bytes(), media_type="audio/wav")


@router.get("/{message_id}/image")
def get_message_image(conversation_id: int, message_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["image_path"] is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(row["image_path"])


MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # mesmo limite já usado para imagem


@router.post("/pdf", response_model=MessageOut, status_code=201)
async def post_pdf_message(
    conversation_id: int, content: str = Form(""), pdf: UploadFile = File(...)
) -> MessageOut:
    if (pdf.content_type or "") != "application/pdf":
        raise HTTPException(status_code=415, detail="file must be a PDF")

    body = await pdf.read()
    if len(body) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="pdf too large (max 10MB)")

    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        dest = _upload_dir() / f"{uuid.uuid4().hex}.pdf"
        dest.write_bytes(body)

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, pdf_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (conversation_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'extract_pdf', 0, ?)",
            (conversation_id, json.dumps({"pdf_path": str(dest), "message_id": message_id})),
        )
        _maybe_enqueue_waiting_agent(conn, conversation_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


@router.get("/{message_id}/pdf")
def get_message_pdf(conversation_id: int, message_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT pdf_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["pdf_path"] is None:
        raise HTTPException(status_code=404, detail="pdf not found")
    return FileResponse(row["pdf_path"], media_type="application/pdf")


@router.delete("/{message_id}", status_code=204)
def delete_message(conversation_id: int, message_id: int) -> Response:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image_path, pdf_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="message not found")

        # Best-effort cleanup: a locked/permission-denied file must not abort the delete —
        # the DB row going away is what matters (it's what leaves the LLM context), losing
        # an orphaned file on disk is a much smaller problem than a message that refuses
        # to delete because of an unrelated filesystem error.
        for path_value in (row["image_path"], row["pdf_path"]):
            if path_value:
                try:
                    Path(path_value).unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            (_tts_cache_dir() / f"{message_id}.wav").unlink(missing_ok=True)
        except OSError:
            pass

        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
