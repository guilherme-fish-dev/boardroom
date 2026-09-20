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
