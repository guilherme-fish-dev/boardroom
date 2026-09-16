import json
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.db import get_connection
from app.mentions import extract_mentions

router = APIRouter(prefix="/api/groups/{group_id}/messages", tags=["messages"])


class MessageIn(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: int
    group_id: int
    sender_type: str
    sender_id: int | None
    content: str
    image_path: str | None
    hidden: bool
    created_at: str


def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        group_id=row["group_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        hidden=bool(row["hidden"]),
        created_at=row["created_at"],
    )


def enqueue_mentions(conn: sqlite3.Connection, group_id: int, trigger_message_id: int, content: str) -> None:
    """Create agent_turn jobs for every mentioned agent that is a member of the group."""
    names = extract_mentions(content)
    if not names:
        return

    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT agents.id FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        WHERE group_members.group_id = ? AND lower(agents.name) IN ({placeholders})
        """,
        (group_id, *names),
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (group_id, row["id"], json.dumps({"trigger_message_id": trigger_message_id})),
        )


@router.get("", response_model=list[MessageOut])
def list_messages(group_id: int, since_id: int | None = None) -> list[MessageOut]:
    conn = get_connection()
    try:
        if since_id is None:
            rows = conn.execute(
                "SELECT * FROM messages WHERE group_id = ? AND hidden = 0 ORDER BY id",
                (group_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE group_id = ? AND hidden = 0 AND id > ? ORDER BY id",
                (group_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


@router.post("", response_model=MessageOut, status_code=201)
def post_message(group_id: int, message: MessageIn) -> MessageOut:
    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        cur = conn.execute(
            "INSERT INTO messages (group_id, sender_type, sender_id, content) "
            "VALUES (?, 'user', NULL, ?)",
            (group_id, message.content),
        )
        message_id = cur.lastrowid
        enqueue_mentions(conn, group_id, message_id, message.content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


def _upload_dir() -> Path:
    path = Path(os.environ.get("BOARDROOM_UPLOAD_DIR", "./data/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/image", response_model=MessageOut, status_code=201)
async def post_image_message(
    group_id: int, content: str = Form(""), image: UploadFile = File(...)
) -> MessageOut:
    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        suffix = Path(image.filename or "upload.png").suffix or ".png"
        dest = _upload_dir() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(await image.read())

        cur = conn.execute(
            "INSERT INTO messages (group_id, sender_type, sender_id, content, image_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (group_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'describe_image', 0, ?)",
            (group_id, json.dumps({"image_path": str(dest), "message_id": message_id})),
        )
        enqueue_mentions(conn, group_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)
