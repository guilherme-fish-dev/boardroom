import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str
    persona_prompt: str
    model_name: str
    vision_capable: bool = False


class AgentOut(AgentIn):
    id: int
    created_at: str


def _row_to_agent(row: sqlite3.Row) -> AgentOut:
    return AgentOut(
        id=row["id"],
        name=row["name"],
        persona_prompt=row["persona_prompt"],
        model_name=row["model_name"],
        vision_capable=bool(row["vision_capable"]),
        created_at=row["created_at"],
    )


@router.get("", response_model=list[AgentOut])
def list_agents() -> list[AgentOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
    finally:
        conn.close()
    return [_row_to_agent(r) for r in rows]


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO agents (name, persona_prompt, model_name, vision_capable) "
                "VALUES (?, ?, ?, ?)",
                (agent.name, agent.persona_prompt, agent.model_name, int(agent.vision_capable)),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_agent(row)


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE agents SET name = ?, persona_prompt = ?, model_name = ?, vision_capable = ? "
            "WHERE id = ?",
            (agent.name, agent.persona_prompt, agent.model_name, int(agent.vision_capable), agent_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_agent(row)
