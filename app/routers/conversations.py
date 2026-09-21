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


class AgentMentionSettingIn(BaseModel):
    human_only_mention: bool


class AgentMentionSettingOut(BaseModel):
    agent_id: int
    human_only_mention: bool


def _require_conversation(conn: sqlite3.Connection, group_id: int, conversation_id: int) -> None:
    conversation = conn.execute(
        "SELECT id FROM conversations WHERE id = ? AND group_id = ?", (conversation_id, group_id)
    ).fetchone()
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")


@router.get("/{conversation_id}/agent-mention-settings", response_model=list[AgentMentionSettingOut])
def list_agent_mention_settings(group_id: int, conversation_id: int) -> list[AgentMentionSettingOut]:
    conn = get_connection()
    try:
        _require_conversation(conn, group_id, conversation_id)
        rows = conn.execute(
            "SELECT agent_id, human_only_mention FROM conversation_agent_mention_settings "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        AgentMentionSettingOut(agent_id=r["agent_id"], human_only_mention=bool(r["human_only_mention"]))
        for r in rows
    ]


@router.put("/{conversation_id}/agent-mention-settings/{agent_id}", response_model=AgentMentionSettingOut)
def set_agent_mention_setting(
    group_id: int, conversation_id: int, agent_id: int, setting: AgentMentionSettingIn
) -> AgentMentionSettingOut:
    conn = get_connection()
    try:
        _require_conversation(conn, group_id, conversation_id)
        member = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND agent_id = ?", (group_id, agent_id)
        ).fetchone()
        if member is None:
            raise HTTPException(status_code=404, detail="agent is not a member of this group")
        conn.execute(
            "INSERT INTO conversation_agent_mention_settings (conversation_id, agent_id, human_only_mention) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (conversation_id, agent_id) DO UPDATE SET human_only_mention = excluded.human_only_mention",
            (conversation_id, agent_id, int(setting.human_only_mention)),
        )
        conn.commit()
    finally:
        conn.close()
    return AgentMentionSettingOut(agent_id=agent_id, human_only_mention=setting.human_only_mention)


@router.put("/{conversation_id}/agent-mention-settings", response_model=list[AgentMentionSettingOut])
def set_all_agent_mention_settings(
    group_id: int, conversation_id: int, setting: AgentMentionSettingIn
) -> list[AgentMentionSettingOut]:
    """'Selecionar todos' shortcut: sets human_only_mention for every current member of the
    conversation's group to the same value in one call. There is no separate persisted
    "conversation-wide" flag — this just bulk-writes the same per-agent rows that
    set_agent_mention_setting writes one at a time, so toggling it off later is a normal
    bulk-clear rather than overriding some other stored state."""
    conn = get_connection()
    try:
        _require_conversation(conn, group_id, conversation_id)
        member_ids = [
            row["agent_id"]
            for row in conn.execute(
                "SELECT agent_id FROM group_members WHERE group_id = ?", (group_id,)
            ).fetchall()
        ]
        for agent_id in member_ids:
            conn.execute(
                "INSERT INTO conversation_agent_mention_settings (conversation_id, agent_id, human_only_mention) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (conversation_id, agent_id) DO UPDATE SET human_only_mention = excluded.human_only_mention",
                (conversation_id, agent_id, int(setting.human_only_mention)),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT agent_id, human_only_mention FROM conversation_agent_mention_settings "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        AgentMentionSettingOut(agent_id=r["agent_id"], human_only_mention=bool(r["human_only_mention"]))
        for r in rows
    ]


class StopResultOut(BaseModel):
    cancelled_pending: int
    still_processing: int


@router.post("/{conversation_id}/stop", response_model=StopResultOut)
def stop_conversation(group_id: int, conversation_id: int) -> StopResultOut:
    """Cancel every job still queued (status='pending') for this conversation, so a chain of
    agents mentioning each other stops firing further replies. A job already 'processing' —
    an LLM request already in flight — can't be aborted mid-call (chat_completion has no
    cancellation hook, and killing the server would also orphan and retry it on restart per
    _recover_orphaned_processing_jobs), so it's left to finish; its count is returned so the
    UI can tell the user one more reply may still land.

    Also sets conversations.stopped_at, a durable flag enqueue_mentions checks before creating
    any new job. Without it, that one still-in-flight reply could @mention another agent and
    re-enqueue a job that didn't exist yet when the UPDATE above ran — outside the reach of
    this cancellation, and able to keep the mention chain going indefinitely. The flag is
    cleared again the next time the user sends a message (see _resume_conversation)."""
    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND group_id = ?", (conversation_id, group_id)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        conn.execute(
            "UPDATE conversations SET stopped_at = datetime('now') WHERE id = ?",
            (conversation_id,),
        )
        cur = conn.execute(
            "UPDATE queue_jobs SET status = 'error' WHERE conversation_id = ? AND status = 'pending'",
            (conversation_id,),
        )
        cancelled = cur.rowcount
        still_processing = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND status = 'processing'",
            (conversation_id,),
        ).fetchone()["c"]

        if cancelled > 0 or still_processing > 0:
            if still_processing > 0:
                content = (
                    f"Fila interrompida pelo usuário ({cancelled} resposta(s) pendente(s) cancelada(s)) — "
                    f"{still_processing} resposta já em andamento ainda vai chegar, isso não dá pra interromper."
                )
            else:
                content = f"Fila interrompida pelo usuário ({cancelled} resposta(s) pendente(s) cancelada(s))."
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
                (conversation_id, content),
            )
        conn.commit()
    finally:
        conn.close()
    return StopResultOut(cancelled_pending=cancelled, still_processing=still_processing)


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
