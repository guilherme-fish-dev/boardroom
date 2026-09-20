from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3

import httpx

from app.db import DEFAULT_SETTINGS, get_connection
from app.llm_client import chat_completion
from app.mentions import extract_mentions
from app.routers.messages import enqueue_mentions
from app.web_search import web_search

logger = logging.getLogger(__name__)


def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def _get_max_history_messages(conn: sqlite3.Connection) -> int:
    val = _get_setting(conn, "max_history_messages")
    try:
        n = int(val)
        return n if n > 0 else 40
    except (ValueError, TypeError):
        return 40


def _fetch_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically claim the next pending job: pick a candidate, then flip it to
    'processing' conditioned on it still being 'pending'. This closes a race where two
    worker loops running against the same database file (e.g. two server processes left
    running at once by mistake) could both select the same pending job before either had
    updated its status, and both end up processing — and both inserting a reply for — the
    same job. If another process's UPDATE won that race in between our SELECT and UPDATE,
    our UPDATE's WHERE clause no longer matches (rowcount 0) and we simply return None for
    this cycle instead of double-claiming it; the worker loop's next iteration picks
    whatever is still actually pending."""
    row = conn.execute(
        "SELECT id FROM queue_jobs WHERE status = 'pending' ORDER BY priority ASC, id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE queue_jobs SET status = 'processing' WHERE id = ? AND status = 'pending'",
        (row["id"],),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return conn.execute("SELECT * FROM queue_jobs WHERE id = ?", (row["id"],)).fetchone()


WEB_SEARCH_INSTRUCTIONS = (
    "\n\nVocê pode pesquisar na internet quando precisar de informação atual ou que não sabe. "
    "Para isso, responda usando SOMENTE esta linha, nada mais: BUSCAR: sua consulta aqui. "
    "Você vai receber os resultados da busca e poderá responder normalmente em seguida, "
    "ou buscar de novo (no máximo 3 vezes) se ainda precisar de mais informação."
)


SKIP_INSTRUCTIONS = (
    "\n\nSe você foi mencionado apenas para confirmar, concordar ou reagir, e não tem "
    "nada de substância para acrescentar, responda usando SOMENTE isto, nada mais: [[SKIP]]. "
    "Da mesma forma, se outro agente já solicitou dados, formulários ou fez perguntas ao usuário e a conversa "
    "está aguardando a resposta dele, NUNCA repita as perguntas e NÃO responda apenas para dizer que está "
    "aguardando ou em prontidão: responda usando SOMENTE [[SKIP]]. "
    "Isso significa que você optou por não responder e nenhuma mensagem sua será publicada."
)

SKIP_MARKER = "[[SKIP]]"

WAIT_USER_INSTRUCTIONS = (
    "\n\nSe a sua resposta solicitar dados, números, preenchimento de campos, respostas a perguntas "
    "ou qualquer decisão/ação do usuário antes que a discussão possa continuar, termine sua resposta "
    "com a tag [[AGUARDANDO_USUARIO]]. Isso pausará automaticamente a fila dos demais agentes para "
    "economizar contexto até o usuário responder."
)

WAIT_USER_MARKERS = ["[[AGUARDANDO_USUARIO]]", "[[WAIT_USER]]"]


def _mention_instructions(other_agent_names: list[str]) -> str:
    if not other_agent_names:
        return ""
    names_list = ", ".join(f"@{name}" for name in other_agent_names)
    return (
        "\n\nVocê também pode mencionar outros agentes deste grupo escrevendo @nome-exato "
        "em qualquer parte da sua resposta, para trazer a opinião deles pra conversa ou "
        "encadear uma sequência de respostas (ex.: pedir pra outro agente validar ou "
        "continuar o que você disse). Use o nome exato cadastrado do agente. "
        f"Agentes deste grupo que você pode mencionar: {names_list}."
    )

# Ancorado ao início de linha (não à string inteira) pra pegar o padrão mesmo quando o
# modelo escreve um preâmbulo numa linha separada antes de "BUSCAR: ...". Deliberadamente
# NÃO detecta "BUSCAR:" no meio de uma frase (ex.: "minha resposta sobre BUSCAR: conceito") —
# isso evitaria falso positivo (busca disparada por engano) às custas de eventualmente perder
# um preâmbulo que fica na MESMA linha do comando (ex.: "Vou pesquisar. BUSCAR: x"), que nesse
# caso vaza como texto normal — um risco menor que ativar uma busca indevida.
SEARCH_PATTERN = re.compile(r"^\s*BUSCAR:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
MAX_SEARCHES_PER_TURN = 3


def _other_group_agent_names(conn: sqlite3.Connection, conversation_id: int, agent_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT agents.name FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        JOIN conversations ON conversations.group_id = group_members.group_id
        WHERE conversations.id = ? AND agents.id != ?
        ORDER BY agents.name
        """,
        (conversation_id, agent_id),
    ).fetchall()
    return [row["name"] for row in rows]


def _build_history(
    conn: sqlite3.Connection,
    conversation_id: int,
    agent_persona: str,
    agent_id: int,
    *,
    exclude_image_descriptions: bool = False,
    max_messages: int | None = None,
) -> list[dict]:
    if max_messages is None:
        max_messages = _get_max_history_messages(conn)

    query = "SELECT sender_type, sender_id, content FROM messages WHERE conversation_id = ?"
    if exclude_image_descriptions:
        query += " AND (hidden = 0 OR IFNULL(hidden_kind, '') != 'image_description')"
    query += " ORDER BY id"
    rows = conn.execute(query, (conversation_id,)).fetchall()

    total_rows = len(rows)
    omitted = 0
    if max_messages > 0 and total_rows > max_messages:
        omitted = total_rows - max_messages
        rows = rows[-max_messages:]

    other_names = _other_group_agent_names(conn, conversation_id, agent_id)
    agent_names = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM agents")}
    system_content = (
        agent_persona
        + WEB_SEARCH_INSTRUCTIONS
        + SKIP_INSTRUCTIONS
        + WAIT_USER_INSTRUCTIONS
        + _mention_instructions(other_names)
    )
    messages = [{"role": "system", "content": system_content}]

    if omitted > 0:
        messages.append({
            "role": "system",
            "content": f"[Histórico anterior ({omitted} mensagens) foi condensado/omitido para priorizar o contexto recente desta conversa.]",
        })

    for row in rows:
        if row["sender_type"] == "agent" and row["sender_id"] == agent_id:
            # This agent's own past turn: keep it as its own "assistant" voice, so the
            # model recognizes it as something *it* said.
            messages.append({"role": "assistant", "content": row["content"]})
        elif row["sender_type"] == "agent":
            # Another agent's turn, from this agent's point of view, is external input —
            # not this model's own words. Without the name prefix, every agent's reply
            # collapses onto the same "assistant" role, so a model looking back at its
            # history sees another agent's message as something *it* already said, and
            # can end up just repeating it back instead of responding.
            name = agent_names.get(row["sender_id"], "outro agente")
            messages.append({"role": "user", "content": f"{name}: {row['content']}"})
        else:
            messages.append({"role": "user", "content": row["content"]})
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
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'image_description')",
        (job["conversation_id"], description),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))


def _find_recent_image(conn: sqlite3.Connection, conversation_id: int) -> str | None:
    row = conn.execute(
        "SELECT image_path FROM messages WHERE conversation_id = ? AND image_path IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row["image_path"] if row else None


def _process_agent_turn(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (job["agent_id"],)).fetchone()
    base_url = _get_setting(conn, "llama_swap_base_url")

    image_base64 = None
    if agent["vision_capable"]:
        image_path = _find_recent_image(conn, job["conversation_id"])
        if image_path:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("ascii")

    history = _build_history(
        conn,
        job["conversation_id"],
        agent["persona_prompt"],
        agent["id"],
        exclude_image_descriptions=image_base64 is not None,
    )

    try:
        reply = chat_completion(
            base_url=base_url,
            model=agent["model_name"],
            messages=history,
            image_base64=image_base64,
        )
    except httpx.HTTPStatusError as exc:
        err_text = str(exc).casefold()
        if "exceed" in err_text and ("context" in err_text or "token" in err_text):
            logger.warning(f"Contexto excedido para job {job['id']}. Tentando fallback com histórico reduzido...")
            reduced_history = _build_history(
                conn,
                job["conversation_id"],
                agent["persona_prompt"],
                agent["id"],
                exclude_image_descriptions=image_base64 is not None,
                max_messages=15,
            )
            reply = chat_completion(
                base_url=base_url,
                model=agent["model_name"],
                messages=reduced_history,
                image_base64=image_base64,
            )
            history = reduced_history
        else:
            raise

    searches_done = 0
    match = SEARCH_PATTERN.search(reply)
    while match and searches_done < MAX_SEARCHES_PER_TURN:
        query = match.group(1).strip()
        try:
            results = web_search(query)
        except Exception as exc:
            results = f"Erro ao buscar: {exc}"

        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
            "VALUES (?, 'system', ?, 1, 'search_result')",
            (job["conversation_id"], f'Busca por "{query}":\n{results}'),
        )

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": f'Resultados da busca por "{query}":\n{results}'})
        reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)
        searches_done += 1
        match = SEARCH_PATTERN.search(reply)

    if match:
        history.append({"role": "assistant", "content": reply})
        history.append(
            {
                "role": "user",
                "content": "Você atingiu o limite de buscas para esta resposta. Responda com base "
                "no que você já sabe, sem buscar de novo.",
            }
        )
        reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)
        if SEARCH_PATTERN.search(reply):
            reply = "Não consegui concluir a busca a tempo, mas posso ajudar com o que já sei — pode perguntar de novo."

    if reply.strip().casefold() == SKIP_MARKER.casefold():
        conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
        return

    needs_user_action = False
    for marker in WAIT_USER_MARKERS:
        if marker.casefold() in reply.casefold():
            needs_user_action = True
            reply = re.sub(re.escape(marker), "", reply, flags=re.IGNORECASE).strip()

    # Fallback heurístico: se há campos de preenchimento (ex: 'R$ _____') direcionados ao usuário
    if not needs_user_action and re.search(r"R\$\s*_{3,}|_{5,}", reply):
        needs_user_action = True

    hidden_kind = "wait_user" if needs_user_action else None

    cur = conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content, hidden_kind) "
        "VALUES (?, 'agent', ?, ?, ?)",
        (job["conversation_id"], agent["id"], reply, hidden_kind),
    )
    agent_message_id = cur.lastrowid

    if needs_user_action:
        conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
        cur_cancel = conn.execute(
            "UPDATE queue_jobs SET status = 'error' WHERE conversation_id = ? AND status = 'pending'",
            (job["conversation_id"],),
        )
        logger.info(
            f"Agente {agent['name']} aguarda ação do usuário. Fila pausada ({cur_cancel.rowcount} pendentes cancelados)."
        )
        return

    # Count active jobs (this job is still 'processing' at this point) to decide whether the
    # conversation's queue has room for a follow-up job from this reply. Antes da introdução de
    # múltiplas conversas por grupo, esse limite era por grupo; agora é por conversa individual —
    # um grupo com várias conversas ativas pode ter mais jobs simultâneos no total do que antes.
    # A setting continua se chamando "max_pending_per_group" (nome desatualizado) porque renomeá-la
    # tocaria settings.py e o frontend, fora do escopo desta migração.
    max_pending = int(_get_setting(conn, "max_pending_per_group") or "20")
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND status IN ('pending','processing')",
        (job["conversation_id"],),
    ).fetchone()["c"]

    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))

    if active_count >= max_pending:
        if extract_mentions(reply):
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["conversation_id"], "Limite de fila atingido nesta conversa — novas menções foram ignoradas até a fila esvaziar."),
            )
        return

    enqueue_mentions(conn, job["conversation_id"], agent_message_id, reply, author_agent_id=agent["id"])


def process_next_job() -> bool:
    """Process exactly one pending job (highest priority, then oldest). Returns False if queue was empty."""
    conn = get_connection()
    try:
        job = _fetch_next_job(conn)
        if job is None:
            return False

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
                "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["conversation_id"], f"Erro ao processar job {job['id']}: {exc}"),
            )
            conn.commit()
        return True
    finally:
        conn.close()
