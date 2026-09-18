import json
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_connection
from app.mentions import extract_mentions

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
    hidden: bool
    created_at: str


def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        hidden=bool(row["hidden"]),
        created_at=row["created_at"],
    )


def enqueue_mentions(conn: sqlite3.Connection, conversation_id: int, trigger_message_id: int, content: str) -> None:
    """Create agent_turn jobs for every mentioned agent that is a member of the conversation's group."""
    names = extract_mentions(content)
    if not names:
        return

    conversation = conn.execute(
        "SELECT group_id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        return

    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT agents.id FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        WHERE group_members.group_id = ? AND lower(agents.name) IN ({placeholders})
        """,
        (conversation["group_id"], *names),
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (conversation_id, row["id"], json.dumps({"trigger_message_id": trigger_message_id})),
        )


@router.get("", response_model=list[MessageOut])
def list_messages(conversation_id: int, since_id: int | None = None) -> list[MessageOut]:
    conn = get_connection()
    try:
        if since_id is None:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND hidden = 0 ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND hidden = 0 AND id > ? ORDER BY id",
                (conversation_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


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
        enqueue_mentions(conn, conversation_id, message_id, message.content)
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
        enqueue_mentions(conn, conversation_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


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
