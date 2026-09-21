#!/usr/bin/env python3
"""
Seed script: Cadastra o time de produção de clipe animado infantil no Boardroom.
Lê docs/time-producao-clipe-infantil.md, extrai o Prompt Base e cada agente,
e cria/atualiza os registros no banco SQLite (data/boardroom.db).
"""

import os
import re
import sqlite3
import sys
from pathlib import Path

# Garante saída UTF-8 no Windows PowerShell
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "time-producao-clipe-infantil.md"
DB_PATH = Path(os.environ.get("BOARDROOM_DB_PATH", REPO_ROOT / "data" / "boardroom.db"))

DEFAULT_MODEL = "gemma4-12b-uncensored-hauhau"
CATEGORY_NAME = "Produção Musical Infantil"
GROUP_NAME = "Clipe Infantil"
GROUP_ICON = "🎬"

AGENT_METADATA = [
    {
        "section_id": "3.1",
        "name": "Diretor Geral",
        "subtitle": "Showrunner — orquestra o time por fases, decide e entrega o pacote final",
        "vision_capable": 0,
        "include_section_5": True,
    },
    {
        "section_id": "3.2",
        "name": "Analista de Letra",
        "subtitle": "Analista de Letra e Ritmo — decupagem em takes, métrica e tempos (SRT/sílabas)",
        "vision_capable": 0,
    },
    {
        "section_id": "3.3",
        "name": "Diretor de Arte",
        "subtitle": "Diretor de Arte e Personagens — bíblia visual, frase de estilo e consistência",
        "vision_capable": 1,
    },
    {
        "section_id": "3.4",
        "name": "Diretor de Animação",
        "subtitle": "Diretor de Animação e Câmera — enquadramento, movimento, energia e transições",
        "vision_capable": 0,
    },
    {
        "section_id": "3.5",
        "name": "Pedagogo",
        "subtitle": "Pedagogo Infantil — adequação etária, valor educativo e segurança sensorial",
        "vision_capable": 0,
    },
    {
        "section_id": "3.6",
        "name": "Estrategista de Retenção",
        "subtitle": "Estrategista de Retenção — ganchos, surpresas a cada 15-20s e participação",
        "vision_capable": 0,
    },
    {
        "section_id": "3.7",
        "name": "Engenheiro de Prompts de Imagem",
        "subtitle": "Gera prompts do primeiro e último quadro de cada take para modelo T2I",
        "vision_capable": 0,
    },
    {
        "section_id": "3.8",
        "name": "Engenheiro de Prompts de Vídeo",
        "subtitle": "Gera prompts de animação e movimento (imagem para vídeo) com durações e negativos",
        "vision_capable": 0,
    },
    {
        "section_id": "3.9",
        "name": "Estrategista de Publicação",
        "subtitle": "Títulos otimizados, descrição, tags, hashtags e conceito de thumbnail para YouTube",
        "vision_capable": 0,
    },
    {
        "section_id": "3.10",
        "name": "Auditor YouTube",
        "subtitle": "Auditor YouTube e Segurança Infantil — conformidade de políticas e diretrizes",
        "vision_capable": 0,
    },
    {
        "section_id": "3.11",
        "name": "Supervisor de Continuidade",
        "subtitle": "Supervisor de Continuidade e Prazos — conferência matemática de tempos e consistência",
        "vision_capable": 0,
    },
]


def load_spec() -> tuple[str, dict[str, str], str]:
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"Arquivo de especificação não encontrado: {SPEC_PATH}")

    content = SPEC_PATH.read_text(encoding="utf-8")

    # Extrai PROMPT BASE (seção 1)
    base_match = re.search(r"## 1\. PROMPT BASE.*?\n```\n(.*?)\n```", content, re.DOTALL)
    if not base_match:
        raise ValueError("Não foi possível extrair a seção 1 (PROMPT BASE)")
    prompt_base = base_match.group(1).strip()

    # Extrai cada agente da seção 3
    # Formato: ### 3.X Titulo\n\n```\n(conteudo)\n```
    agent_blocks = {}
    pattern = re.compile(r"### (3\.\d+)[^\n]*\n\n```\n(.*?)\n```", re.DOTALL)
    for m in pattern.finditer(content):
        sec_id = m.group(1)
        block = m.group(2).strip()
        agent_blocks[sec_id] = block

    # Extrai Seção 5 (Formato do Pacote Final)
    sec5_match = re.search(r"## 5\. FORMATO DO PACOTE FINAL.*?\n```\n(.*?)\n```", content, re.DOTALL)
    sec5 = sec5_match.group(1).strip() if sec5_match else ""

    return prompt_base, agent_blocks, sec5


def run_seed():
    print(f"[Seed] Lendo especificação em: {SPEC_PATH}")
    prompt_base, agent_blocks, sec5 = load_spec()
    print(f"[Seed] Prompt base carregado ({len(prompt_base)} caracteres).")
    print(f"[Seed] Blocos de agentes encontrados: {list(agent_blocks.keys())}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # 1. Categoria
        cur = conn.execute(
            "SELECT id FROM agent_categories WHERE name = ?", (CATEGORY_NAME,)
        )
        cat_row = cur.fetchone()
        if cat_row:
            category_id = cat_row["id"]
            print(f"[Seed] Categoria existente '{CATEGORY_NAME}' (ID {category_id})")
        else:
            cur = conn.execute(
                "INSERT INTO agent_categories (name) VALUES (?)", (CATEGORY_NAME,)
            )
            category_id = cur.lastrowid
            print(f"[Seed] Categoria criada '{CATEGORY_NAME}' (ID {category_id})")

        # 2. Grupo
        cur = conn.execute("SELECT id FROM groups WHERE name = ?", (GROUP_NAME,))
        group_row = cur.fetchone()
        if group_row:
            group_id = group_row["id"]
            conn.execute("UPDATE groups SET icon = ? WHERE id = ?", (GROUP_ICON, group_id))
            print(f"[Seed] Grupo existente '{GROUP_NAME}' (ID {group_id})")
        else:
            cur = conn.execute(
                "INSERT INTO groups (name, icon) VALUES (?, ?)", (GROUP_NAME, GROUP_ICON)
            )
            group_id = cur.lastrowid
            print(f"[Seed] Grupo criado '{GROUP_NAME}' (ID {group_id}) com ícone {GROUP_ICON}")

        # Garante conversa "Geral" para o grupo
        cur = conn.execute(
            "SELECT id FROM conversations WHERE group_id = ? AND name = 'Geral'", (group_id,)
        )
        if not cur.fetchone():
            conn.execute(
                "INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (group_id,)
            )
            print(f"[Seed] Conversa 'Geral' criada para o grupo {group_id}")

        # 3. Agentes
        agent_ids = []
        for meta in AGENT_METADATA:
            sec_id = meta["section_id"]
            name = meta["name"]
            subtitle = meta["subtitle"]
            vision = meta.get("vision_capable", 0)

            if sec_id not in agent_blocks:
                print(f"[Aviso] Seção {sec_id} não encontrada para o agente '{name}'!")
                continue

            agent_block = agent_blocks[sec_id]
            if meta.get("include_section_5") and sec5:
                agent_block += f"\n\nFORMATO DO PACOTE FINAL (SEÇÃO 5):\n```\n{sec5}\n```"

            full_persona = f"{prompt_base}\n\n---\n\n{agent_block}"

            # Verifica se agente já existe
            cur = conn.execute("SELECT id FROM agents WHERE name = ?", (name,))
            existing = cur.fetchone()
            if existing:
                agent_id = existing["id"]
                conn.execute(
                    """
                    UPDATE agents
                    SET persona_prompt = ?, subtitle = ?, model_name = ?, vision_capable = ?
                    WHERE id = ?
                    """,
                    (full_persona, subtitle, DEFAULT_MODEL, vision, agent_id),
                )
                print(f"[Seed] Agente atualizado: '{name}' (ID {agent_id})")
            else:
                cur = conn.execute(
                    """
                    INSERT INTO agents (name, persona_prompt, subtitle, model_name, vision_capable)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, full_persona, subtitle, DEFAULT_MODEL, vision),
                )
                agent_id = cur.lastrowid
                print(f"[Seed] Agente criado: '{name}' (ID {agent_id})")

            agent_ids.append(agent_id)

            # Associa à Categoria
            conn.execute(
                "INSERT OR IGNORE INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
                (category_id, agent_id),
            )

            # Associa ao Grupo
            conn.execute(
                "INSERT OR IGNORE INTO group_members (group_id, agent_id) VALUES (?, ?)",
                (group_id, agent_id),
            )

        conn.commit()
        print(f"\n[Sucesso] {len(agent_ids)} agentes cadastrados/atualizados no grupo '{GROUP_NAME}'!")

    finally:
        conn.close()


if __name__ == "__main__":
    run_seed()
