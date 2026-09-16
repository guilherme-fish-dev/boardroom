import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupIn(BaseModel):
    name: str


class GroupOut(GroupIn):
    id: int
    created_at: str


class MemberIn(BaseModel):
    agent_id: int


class MemberOut(BaseModel):
    id: int
    name: str


@router.get("", response_model=list[GroupOut])
def list_groups() -> list[GroupOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM groups ORDER BY id").fetchall()
    finally:
        conn.close()
    return [GroupOut(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in rows]


@router.post("", response_model=GroupOut, status_code=201)
def create_group(group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (group.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], created_at=row["created_at"])


@router.get("/{group_id}/members", response_model=list[MemberOut])
def list_members(group_id: int) -> list[MemberOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT agents.id, agents.name FROM group_members "
            "JOIN agents ON agents.id = group_members.agent_id "
            "WHERE group_members.group_id = ? ORDER BY agents.id",
            (group_id,),
        ).fetchall()
    finally:
        conn.close()
    return [MemberOut(id=r["id"], name=r["name"]) for r in rows]


@router.post("/{group_id}/members", status_code=204)
def add_member(group_id: int, member: MemberIn) -> Response:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, agent_id) VALUES (?, ?)",
            (group_id, member.agent_id),
        )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.delete("/{group_id}/members/{agent_id}", status_code=204)
def remove_member(group_id: int, agent_id: int) -> Response:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND agent_id = ?",
            (group_id, agent_id),
        )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
