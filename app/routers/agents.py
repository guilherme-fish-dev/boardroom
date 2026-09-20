import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from app.db import get_connection, get_setting
from app.llm_client import chat_completion

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str
    persona_prompt: str
    subtitle: str = ""
    model_name: str
    vision_capable: bool = False
    category_ids: list[int] = []

    @field_validator("name")
    @classmethod
    def name_not_reserved(cls, value: str) -> str:
        # "all" is a reserved mention keyword (@all fans out to every group member, see
        # enqueue_mentions in app/routers/messages.py) — an agent with this literal name
        # would never be individually mentionable.
        if value.strip().lower() == "all":
            raise ValueError('"all" is a reserved name and can\'t be used for an agent')
        return value


class AgentOut(AgentIn):
    id: int
    created_at: str


def _category_ids_for_agent(conn: sqlite3.Connection, agent_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT category_id FROM agent_category_members WHERE agent_id = ? ORDER BY category_id",
        (agent_id,),
    ).fetchall()
    return [r["category_id"] for r in rows]


def _sync_agent_categories(conn: sqlite3.Connection, agent_id: int, category_ids: list[int]) -> None:
    conn.execute("DELETE FROM agent_category_members WHERE agent_id = ?", (agent_id,))
    for category_id in category_ids:
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category_id, agent_id),
        )


def _row_to_agent(conn: sqlite3.Connection, row: sqlite3.Row) -> AgentOut:
    return AgentOut(
        id=row["id"],
        name=row["name"],
        persona_prompt=row["persona_prompt"],
        subtitle=row["subtitle"],
        model_name=row["model_name"],
        vision_capable=bool(row["vision_capable"]),
        created_at=row["created_at"],
        category_ids=_category_ids_for_agent(conn, row["id"]),
    )


@router.get("", response_model=list[AgentOut])
def list_agents() -> list[AgentOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
        result = [_row_to_agent(conn, r) for r in rows]
    finally:
        conn.close()
    return result


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO agents (name, persona_prompt, subtitle, model_name, vision_capable) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    agent.name,
                    agent.persona_prompt,
                    agent.subtitle,
                    agent.model_name,
                    int(agent.vision_capable),
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        agent_id = cur.lastrowid
        try:
            _sync_agent_categories(conn, agent_id, agent.category_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="invalid category_id")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        result = _row_to_agent(conn, row)
    finally:
        conn.close()
    return result


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "UPDATE agents SET name = ?, persona_prompt = ?, subtitle = ?, model_name = ?, "
                "vision_capable = ? WHERE id = ?",
                (
                    agent.name,
                    agent.persona_prompt,
                    agent.subtitle,
                    agent.model_name,
                    int(agent.vision_capable),
                    agent_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            _sync_agent_categories(conn, agent_id, agent.category_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="invalid category_id")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        result = _row_to_agent(conn, row)
    finally:
        conn.close()
    return result


PERSONA_SYSTEM_PROMPT = (
    "Você expande um esboço curto de persona num system prompt detalhado para um agente de "
    "IA que participa de conversas em grupo com outros agentes de IA. Mantenha o ponto de "
    "vista central do esboço, adicione traços de personalidade, forma de argumentar, e "
    "limites claros de comportamento. Responda só com o texto do system prompt final, sem "
    "comentários extras."
)


class GeneratePersonaIn(BaseModel):
    draft: str
    agent_name: str = ""


class GeneratePersonaOut(BaseModel):
    persona_prompt: str


@router.post("/generate-persona", response_model=GeneratePersonaOut)
def generate_persona(payload: GeneratePersonaIn) -> GeneratePersonaOut:
    conn = get_connection()
    try:
        assistant_model = get_setting(conn, "assistant_model")
        base_url = get_setting(conn, "llama_swap_base_url")
    finally:
        conn.close()

    if not assistant_model:
        raise HTTPException(
            status_code=400,
            detail="configure um modelo assistente em Configurações antes de usar essa função",
        )

    user_content = payload.draft
    if payload.agent_name:
        user_content = f"Nome do agente: {payload.agent_name}\n\nEsboço: {payload.draft}"

    try:
        persona_prompt = chat_completion(
            base_url=base_url,
            model=assistant_model,
            messages=[
                {"role": "system", "content": PERSONA_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"não foi possível gerar a persona: {exc}")

    return GeneratePersonaOut(persona_prompt=persona_prompt)


@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: int) -> Response:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
