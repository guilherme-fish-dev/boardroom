import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/agent-categories", tags=["agent-categories"])


class AgentCategoryIn(BaseModel):
    name: str


class AgentCategoryOut(AgentCategoryIn):
    id: int
    created_at: str
    agent_count: int


class AgentCategoryMemberOut(BaseModel):
    id: int
    name: str


class AgentCategoryMemberIds(BaseModel):
    agent_ids: list[int]


@router.get("", response_model=list[AgentCategoryOut])
def list_agent_categories() -> list[AgentCategoryOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT agent_categories.*, "
            "(SELECT COUNT(*) FROM agent_category_members "
            " WHERE agent_category_members.category_id = agent_categories.id) AS agent_count "
            "FROM agent_categories ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        AgentCategoryOut(
            id=r["id"], name=r["name"], created_at=r["created_at"], agent_count=r["agent_count"]
        )
        for r in rows
    ]


@router.post("", response_model=AgentCategoryOut, status_code=201)
def create_agent_category(category: AgentCategoryIn) -> AgentCategoryOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute("INSERT INTO agent_categories (name) VALUES (?)", (category.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="category name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM agent_categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return AgentCategoryOut(id=row["id"], name=row["name"], created_at=row["created_at"], agent_count=0)


@router.delete("/{category_id}", status_code=204)
def delete_agent_category(category_id: int) -> Response:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM agent_categories WHERE id = ?", (category_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="category not found")
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.get("/{category_id}/members", response_model=list[AgentCategoryMemberOut])
def list_agent_category_members(category_id: int) -> list[AgentCategoryMemberOut]:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        rows = conn.execute(
            "SELECT agents.id, agents.name FROM agent_category_members "
            "JOIN agents ON agents.id = agent_category_members.agent_id "
            "WHERE agent_category_members.category_id = ? ORDER BY agents.id",
            (category_id,),
        ).fetchall()
    finally:
        conn.close()
    return [AgentCategoryMemberOut(id=r["id"], name=r["name"]) for r in rows]


@router.post("/{category_id}/members/add", status_code=204)
def add_agent_category_members(category_id: int, payload: AgentCategoryMemberIds) -> Response:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")

        # Validar a existência de todos os agent_ids ANTES de inserir, em vez de confiar
        # em "INSERT OR IGNORE" sozinho: OR IGNORE também suprime a violação de FK de um
        # agent_id inexistente (mesma armadilha já vista na sincronização de categorias
        # em app/routers/agents.py), o que faria um id inválido ser silenciosamente
        # ignorado em vez de retornar 400. A validação é feita em uma única query batched
        # (IN (...)) em vez de uma SELECT por id. Validando antes, o INSERT OR IGNORE
        # abaixo só precisa lidar com o caso inofensivo de "já é membro" (colisão de PK).
        agent_ids = list(dict.fromkeys(payload.agent_ids))
        if agent_ids:
            placeholders = ",".join("?" * len(agent_ids))
            existing_ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM agents WHERE id IN ({placeholders})", agent_ids
                ).fetchall()
            }
            missing_ids = [agent_id for agent_id in agent_ids if agent_id not in existing_ids]
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"agents not found: {missing_ids}")

        for agent_id in agent_ids:
            conn.execute(
                "INSERT OR IGNORE INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
                (category_id, agent_id),
            )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.post("/{category_id}/members/remove", status_code=204)
def remove_agent_category_members(category_id: int, payload: AgentCategoryMemberIds) -> Response:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        # Sem validação prévia de agent_ids aqui: diferente do INSERT em add_agent_category_members,
        # um DELETE contra uma linha inexistente (agent_id inválido ou não membro) já é um no-op
        # seguro, então não há erro silencioso a evitar.
        for agent_id in payload.agent_ids:
            conn.execute(
                "DELETE FROM agent_category_members WHERE category_id = ? AND agent_id = ?",
                (category_id, agent_id),
            )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
