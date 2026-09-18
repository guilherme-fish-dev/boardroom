import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/groups/{group_id}/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    name: str


class ConversationOut(BaseModel):
    id: int
    group_id: int
    name: str
    created_at: str


def _row_to_conversation(row: sqlite3.Row) -> ConversationOut:
    return ConversationOut(
        id=row["id"], group_id=row["group_id"], name=row["name"], created_at=row["created_at"]
    )


@router.get("", response_model=list[ConversationOut])
def list_conversations(group_id: int) -> list[ConversationOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE group_id = ? ORDER BY id", (group_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_conversation(r) for r in rows]


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(group_id: int, conversation: ConversationIn) -> ConversationOut:
    name = conversation.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="conversation name is required")

    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        cur = conn.execute(
            "INSERT INTO conversations (group_id, name) VALUES (?, ?)", (group_id, name)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_conversation(row)


@router.put("/{conversation_id}", response_model=ConversationOut)
def update_conversation(group_id: int, conversation_id: int, conversation: ConversationIn) -> ConversationOut:
    name = conversation.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="conversation name is required")

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE conversations SET name = ? WHERE id = ? AND group_id = ?",
            (name, conversation_id, group_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation not found")
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_conversation(row)


@router.delete("/{conversation_id}", response_model=list[ConversationOut])
def delete_conversation(group_id: int, conversation_id: int) -> list[ConversationOut]:
    """Delete a conversation. If it was the group's last one, a new empty 'Geral'
    conversation is created automatically so the group is never left without one.
    Unlike other DELETE endpoints in this API (which return 204), this one returns
    200 with the group's updated conversation list, since the caller needs to learn
    the id of a replacement 'Geral' conversation when one gets auto-created."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND group_id = ?",
            (conversation_id, group_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation not found")

        remaining = conn.execute(
            "SELECT * FROM conversations WHERE group_id = ? ORDER BY id", (group_id,)
        ).fetchall()
        if not remaining:
            new_cur = conn.execute(
                "INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (group_id,)
            )
            conn.commit()
            remaining = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (new_cur.lastrowid,)
            ).fetchall()
        else:
            conn.commit()
    finally:
        conn.close()
    return [_row_to_conversation(r) for r in remaining]
