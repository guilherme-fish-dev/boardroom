from __future__ import annotations

import base64
import json
import logging
import sqlite3

from app.db import get_connection
from app.llm_client import chat_completion
from app.mentions import extract_mentions
from app.routers.messages import enqueue_mentions

logger = logging.getLogger(__name__)


def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _fetch_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM queue_jobs WHERE status = 'pending' "
        "ORDER BY priority ASC, id ASC LIMIT 1"
    ).fetchone()


WEB_SEARCH_INSTRUCTIONS = (
    "\n\nVocê pode pesquisar na internet quando precisar de informação atual ou que não sabe. "
    "Para isso, responda usando SOMENTE esta linha, nada mais: BUSCAR: sua consulta aqui. "
    "Você vai receber os resultados da busca e poderá responder normalmente em seguida, "
    "ou buscar de novo (no máximo 3 vezes) se ainda precisar de mais informação."
)


def _build_history(
    conn: sqlite3.Connection,
    group_id: int,
    agent_persona: str,
    *,
    exclude_image_descriptions: bool = False,
) -> list[dict]:
    query = "SELECT sender_type, sender_id, content FROM messages WHERE group_id = ?"
    if exclude_image_descriptions:
        query += " AND (hidden = 0 OR IFNULL(hidden_kind, '') != 'image_description')"
    query += " ORDER BY id"
    rows = conn.execute(query, (group_id,)).fetchall()
    messages = [{"role": "system", "content": agent_persona + WEB_SEARCH_INSTRUCTIONS}]
    for row in rows:
        role = "assistant" if row["sender_type"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})
    return messages


def _process_describe_image(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    payload = json.loads(job["payload"])
    vision_model = _get_setting(conn, "default_vision_model")
    base_url = _get_setting(conn, "llama_swap_base_url")

    with open(payload["image_path"], "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("ascii")

    description = chat_completion(
        base_url=base_url,
        model=vision_model,
        messages=[{"role": "user", "content": "Descreva esta imagem com o máximo de detalhes e precisão possível."}],
        image_base64=image_base64,
    )

    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'image_description')",
        (job["group_id"], description),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))


def _find_recent_image(conn: sqlite3.Connection, group_id: int) -> str | None:
    row = conn.execute(
        "SELECT image_path FROM messages WHERE group_id = ? AND image_path IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (group_id,),
    ).fetchone()
    return row["image_path"] if row else None


def _process_agent_turn(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (job["agent_id"],)).fetchone()
    base_url = _get_setting(conn, "llama_swap_base_url")

    image_base64 = None
    if agent["vision_capable"]:
        image_path = _find_recent_image(conn, job["group_id"])
        if image_path:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("ascii")

    history = _build_history(
        conn,
        job["group_id"],
        agent["persona_prompt"],
        exclude_image_descriptions=image_base64 is not None,
    )

    reply = chat_completion(
        base_url=base_url,
        model=agent["model_name"],
        messages=history,
        image_base64=image_base64,
    )

    cur = conn.execute(
        "INSERT INTO messages (group_id, sender_type, sender_id, content) "
        "VALUES (?, 'agent', ?, ?)",
        (job["group_id"], agent["id"], reply),
    )
    agent_message_id = cur.lastrowid

    # Count active jobs (this job is still 'processing' at this point) to decide
    # whether the group's queue has room for a follow-up job from this reply.
    max_pending = int(_get_setting(conn, "max_pending_per_group") or "20")
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM queue_jobs WHERE group_id = ? AND status IN ('pending','processing')",
        (job["group_id"],),
    ).fetchone()["c"]

    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))

    if active_count >= max_pending:
        if extract_mentions(reply):
            conn.execute(
                "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["group_id"], "Limite de fila atingido neste grupo — novas menções foram ignoradas até a fila esvaziar."),
            )
        return

    enqueue_mentions(conn, job["group_id"], agent_message_id, reply)


def process_next_job() -> bool:
    """Process exactly one pending job (highest priority, then oldest). Returns False if queue was empty."""
    conn = get_connection()
    try:
        job = _fetch_next_job(conn)
        if job is None:
            return False

        conn.execute("UPDATE queue_jobs SET status = 'processing' WHERE id = ?", (job["id"],))
        conn.commit()

        try:
            if job["job_type"] == "describe_image":
                _process_describe_image(conn, job)
            else:
                _process_agent_turn(conn, job)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - surface any LLM/IO failure as an error job + system message
            logger.exception(f"Erro ao processar job {job['id']}")
            conn.rollback()  # discard any partial, uncommitted work (e.g. the agent reply insert) before recording the error
            conn.execute("UPDATE queue_jobs SET status = 'error' WHERE id = ?", (job["id"],))
            conn.execute(
                "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["group_id"], f"Erro ao processar job {job['id']}: {exc}"),
            )
            conn.commit()
        return True
    finally:
        conn.close()
