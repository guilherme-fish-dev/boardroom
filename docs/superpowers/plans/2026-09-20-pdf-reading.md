# Leitura de PDF pelos agentes — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir anexar um PDF a uma mensagem do chat; o texto é extraído de forma assíncrona (job de fila) e entra no histórico de todo agente do grupo como mensagem oculta, do mesmo jeito que a descrição de imagem já funciona hoje.

**Architecture:** Um novo módulo isolado (`app/pdf_extract.py`) extrai e trunca o texto de um PDF usando `pypdf`. Um novo endpoint (`POST/GET .../messages/pdf`) espelha o endpoint de imagem já existente, salvando o arquivo e criando um job `extract_pdf`. Um novo handler no worker de fila (`_process_extract_pdf`) processa esse job e grava o texto (ou um aviso, se vazio) como mensagem oculta — sem a ramificação por `vision_capable` que a imagem tem, porque todo agente recebe o mesmo texto.

**Tech Stack:** Python, FastAPI, SQLite (`sqlite3` stdlib), `pypdf`, pytest.

Spec de referência: `docs/superpowers/specs/2026-09-20-pdf-reading-design.md`.

---

### Task 1: módulo de extração `app/pdf_extract.py`

**Files:**
- Modify: `requirements.txt`
- Create: `app/pdf_extract.py`
- Test: `tests/test_pdf_extract.py`

- [ ] **Step 1: Adicionar a dependência**

Em `requirements.txt`, adicionar a linha (o projeto já fixa versões exatas em cada linha existente; usar a versão instalada no ambiente):

```
pypdf==6.19.0
```

- [ ] **Step 2: Instalar a dependência no ambiente**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pip install pypdf==6.19.0`
Expected: `Successfully installed pypdf-6.19.0` (ou "Requirement already satisfied" se já estiver instalado)

- [ ] **Step 3: Escrever os testes que falham**

Criar `tests/test_pdf_extract.py`:

```python
import io

from pypdf import PdfReader, PdfWriter

from app.pdf_extract import extract_text


def _minimal_pdf_bytes(lines: list[str]) -> bytes:
    """Build a minimal single-page PDF containing the given lines of text, using the
    standard Helvetica font, entirely by hand (no PDF-generation dependency needed just
    for tests) — good enough for pypdf to read back with extract_text()."""
    content_lines = "\n".join(
        f"BT /F1 24 Tf 72 {700 - i * 30} Td ({line}) Tj ET" for i, line in enumerate(lines)
    )
    content = content_lines.encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    stream = b"stream\n" + content + b"\nendstream\n"
    objects.append(("5 0 obj\n<< /Length %d >>\n" % len(content)).encode("ascii") + stream + b"endobj\n")

    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode("ascii")
    pdf += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref_offset)
    ).encode("ascii")
    return pdf


def _minimal_blank_pdf_bytes() -> bytes:
    """A minimal single-page PDF with no content stream at all — no extractable text."""
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 612 792] >>\nendobj\n",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode("ascii")
    pdf += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref_offset)
    ).encode("ascii")
    return pdf


def test_extract_text_returns_single_page_text(tmp_path):
    pdf_path = tmp_path / "single.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes(["Hello World", "Second line"]))

    result = extract_text(str(pdf_path))

    assert result == "Hello World\nSecond line"


def test_extract_text_concatenates_multiple_pages(tmp_path):
    writer = PdfWriter()
    for text in ["Page one text", "Page two text"]:
        reader = PdfReader(io.BytesIO(_minimal_pdf_bytes([text])))
        writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "multi.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = extract_text(str(pdf_path))

    assert result == "Page one text\n\nPage two text"


def test_extract_text_returns_empty_string_for_blank_page(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    pdf_path.write_bytes(_minimal_blank_pdf_bytes())

    result = extract_text(str(pdf_path))

    assert result == ""


def test_extract_text_truncates_long_text(tmp_path):
    pdf_path = tmp_path / "long.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes(["x" * 50]))

    result = extract_text(str(pdf_path), max_chars=10)

    assert result == "x" * 10 + "\n\n[texto truncado — o PDF tem mais conteúdo do que o mostrado aqui]"


def test_extract_text_raises_for_corrupt_file(tmp_path):
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"not a real pdf file, just random bytes 12345")

    try:
        extract_text(str(pdf_path))
        assert False, "expected an exception for a corrupt PDF"
    except Exception:
        pass
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_pdf_extract.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.pdf_extract'`

- [ ] **Step 5: Implementar `app/pdf_extract.py`**

```python
from __future__ import annotations

from pypdf import PdfReader

MAX_PDF_TEXT_CHARS = 20_000
TRUNCATION_NOTICE = "\n\n[texto truncado — o PDF tem mais conteúdo do que o mostrado aqui]"


def extract_text(pdf_path: str, *, max_chars: int = MAX_PDF_TEXT_CHARS) -> str:
    """Extract and concatenate the text of every page of a PDF, truncating if it exceeds
    max_chars. Returns an empty string (after stripping whitespace) if no page has
    extractable text — e.g. a scanned document with no text layer. Callers decide what to
    do with that case (this module only extracts, it doesn't interpret the result).

    Raises whatever pypdf raises for a corrupt, invalid, or password-protected file — not
    caught here, propagates to the caller."""
    reader = PdfReader(pdf_path)
    pages_text = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages_text).strip()
    if len(full_text) > max_chars:
        return full_text[:max_chars] + TRUNCATION_NOTICE
    return full_text
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_pdf_extract.py -v`
Expected: PASS (5 testes)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/pdf_extract.py tests/test_pdf_extract.py
git commit -m "feat: add PDF text extraction module"
```

---

### Task 2: schema — coluna `pdf_path` e `job_type='extract_pdf'`

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_db.py`:

```python
def test_init_db_adds_pdf_path_column_to_messages(db):
    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()
    assert "pdf_path" in columns


def test_init_db_migrates_queue_jobs_check_to_allow_extract_pdf(tmp_path, monkeypatch):
    from app.db import init_db

    db_file = tmp_path / "old.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT NOT NULL DEFAULT '💬',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE queue_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            agent_id INTEGER,
            job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image')),
            priority INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO groups (id, name) VALUES (1, 'investidores')")
    conn.execute("INSERT INTO conversations (id, group_id, name) VALUES (1, 1, 'Geral')")
    conn.execute(
        "INSERT INTO queue_jobs (id, conversation_id, agent_id, job_type, priority, payload, status) "
        "VALUES (1, 1, NULL, 'describe_image', 0, '{}', 'done')"
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_connection()
    try:
        old_job = conn.execute("SELECT * FROM queue_jobs WHERE id = 1").fetchone()
        cur = conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (1, NULL, 'extract_pdf', 0, '{}')"
        )
        conn.commit()
        new_job = conn.execute("SELECT * FROM queue_jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()

    assert old_job["job_type"] == "describe_image"
    assert old_job["status"] == "done"
    assert new_job["job_type"] == "extract_pdf"
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_db.py -k "pdf_path or extract_pdf" -v`
Expected: FAIL — `test_init_db_adds_pdf_path_column_to_messages` falha porque a coluna não existe; `test_init_db_migrates_queue_jobs_check_to_allow_extract_pdf` falha com `sqlite3.IntegrityError: CHECK constraint failed` no `INSERT` do `new_job`.

- [ ] **Step 3: Atualizar `SCHEMA` em `app/db.py`**

Na definição de `SCHEMA`, alterar a tabela `messages` para incluir `pdf_path`:

```python
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK (sender_type IN ('user','agent','system')),
    sender_id INTEGER,
    content TEXT NOT NULL,
    image_path TEXT,
    pdf_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    hidden_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

E a tabela `queue_jobs` para incluir `'extract_pdf'` no `CHECK`:

```python
CREATE TABLE IF NOT EXISTS queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image','extract_pdf')),
    priority INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Adicionar as duas funções de migração em `app/db.py`**

Logo depois de `_ensure_group_icon_column`:

```python
def _ensure_pdf_path_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "pdf_path" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN pdf_path TEXT")


def _ensure_queue_jobs_allows_extract_pdf(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='queue_jobs'"
    ).fetchone()
    if row is None or "extract_pdf" in row["sql"]:
        return
    conn.execute("ALTER TABLE queue_jobs RENAME TO queue_jobs_old")
    conn.execute(
        "CREATE TABLE queue_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        "agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,"
        "job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image','extract_pdf')),"
        "priority INTEGER NOT NULL,"
        "payload TEXT NOT NULL,"
        "status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),"
        "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute(
        "INSERT INTO queue_jobs SELECT id, conversation_id, agent_id, job_type, priority, "
        "payload, status, created_at FROM queue_jobs_old"
    )
    conn.execute("DROP TABLE queue_jobs_old")
```

- [ ] **Step 5: Chamar as duas funções em `init_db()`**

Em `app/db.py`, na função `init_db()`, adicionar as duas chamadas junto das migrações já existentes:

```python
def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _ensure_hidden_kind_column(conn)
        _ensure_group_icon_column(conn)
        _ensure_pdf_path_column(conn)
        _ensure_queue_jobs_allows_extract_pdf(conn)
        _ensure_conversations_table(conn)
        _recover_orphaned_processing_jobs(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_db.py -v`
Expected: PASS (todos os testes de `test_db.py`, incluindo os 2 novos)

- [ ] **Step 7: Rodar a suíte completa para checar ausência de regressão**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS (todos os testes do projeto)

- [ ] **Step 8: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add pdf_path column and extract_pdf job type"
```

---

### Task 3: endpoint de upload `POST/GET .../messages/pdf`

**Files:**
- Modify: `app/routers/messages.py`
- Test: `tests/test_messages_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_messages_api.py`:

```python
def test_upload_pdf_creates_message_and_priority_job(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "segue o relatório"},
        files=files,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["pdf_path"] is not None
    assert body["conversation_id"] == conversation["id"]

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "extract_pdf"
    assert jobs[0]["priority"] == 0
    assert jobs[0]["conversation_id"] == conversation["id"]


def test_upload_pdf_rejects_non_pdf_content_type(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("notes.txt", b"just text", "text/plain")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "isso não é pdf"},
        files=files,
    )
    assert resp.status_code == 415


def test_upload_pdf_rejects_oversized_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    big_payload = b"x" * (10 * 1024 * 1024 + 1)
    files = {"pdf": ("big.pdf", big_payload, "application/pdf")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "arquivo grande"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_pdf_404_for_unknown_conversation(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    resp = client.post(
        "/api/conversations/9999/messages/pdf",
        data={"content": "relatório"},
        files=files,
    )
    assert resp.status_code == 404


def test_get_message_pdf_returns_file_bytes(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "relatório"},
        files=files,
    ).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/pdf")
    assert resp.status_code == 200
    assert resp.content == b"fake-pdf-bytes"


def test_get_message_pdf_404_when_no_pdf(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "sem pdf"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/pdf")
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_messages_api.py -k pdf -v`
Expected: FAIL — 404/405 nas rotas inexistentes (`/messages/pdf` ainda não existe) e `AssertionError` no campo `pdf_path` (Pydantic ainda não conhece o campo).

- [ ] **Step 3: Adicionar `pdf_path` a `MessageOut` e `_row_to_message`**

Em `app/routers/messages.py`, no topo do arquivo:

```python
class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_type: str
    sender_id: int | None
    content: str
    image_path: str | None
    pdf_path: str | None
    hidden: bool
    hidden_kind: str | None
    created_at: str
```

```python
def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        pdf_path=row["pdf_path"],
        hidden=bool(row["hidden"]),
        hidden_kind=row["hidden_kind"],
        created_at=row["created_at"],
    )
```

- [ ] **Step 4: Adicionar os dois endpoints, logo depois de `get_message_image`**

```python
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # mesmo limite já usado para imagem


@router.post("/pdf", response_model=MessageOut, status_code=201)
async def post_pdf_message(
    conversation_id: int, content: str = Form(""), pdf: UploadFile = File(...)
) -> MessageOut:
    if (pdf.content_type or "") != "application/pdf":
        raise HTTPException(status_code=415, detail="file must be a PDF")

    body = await pdf.read()
    if len(body) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="pdf too large (max 10MB)")

    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        dest = _upload_dir() / f"{uuid.uuid4().hex}.pdf"
        dest.write_bytes(body)

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, pdf_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (conversation_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'extract_pdf', 0, ?)",
            (conversation_id, json.dumps({"pdf_path": str(dest), "message_id": message_id})),
        )
        _maybe_enqueue_waiting_agent(conn, conversation_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


@router.get("/{message_id}/pdf")
def get_message_pdf(conversation_id: int, message_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT pdf_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["pdf_path"] is None:
        raise HTTPException(status_code=404, detail="pdf not found")
    return FileResponse(row["pdf_path"], media_type="application/pdf")
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_messages_api.py -v`
Expected: PASS (todos os testes de `test_messages_api.py`, incluindo os 6 novos de PDF)

- [ ] **Step 6: Rodar a suíte completa**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS (todos os testes do projeto)

- [ ] **Step 7: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: add PDF upload and download endpoints"
```

---

### Task 4: job `extract_pdf` no worker de fila

**Files:**
- Modify: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_queue_worker.py`:

```python
def test_process_next_job_extract_pdf_saves_hidden_message_and_marks_done(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"fake-pdf-bytes")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.extract_text", lambda path, **kwargs: "texto extraído do pdf")

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "texto extraído do pdf"
    assert job["status"] == "done"


def test_process_next_job_extract_pdf_with_no_text_saves_warning_message(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"fake-pdf-bytes")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.extract_text", lambda path, **kwargs: "")

    process_next_job()

    conn = get_connection()
    try:
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'pdf_extract'"
        ).fetchall()
    finally:
        conn.close()

    assert len(hidden_messages) == 1
    assert hidden_messages[0]["content"] == "Nenhum texto extraível encontrado neste PDF (pode ser um documento escaneado)."


def test_process_next_job_extract_pdf_failure_marks_job_error(db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"not a pdf")

    conn = get_connection()
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'extract_pdf', 0, ?)",
        (conversation_id, json.dumps({"pdf_path": str(pdf_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    def _raise(path, **kwargs):
        raise RuntimeError("pdf inválido")

    monkeypatch.setattr("app.queue_worker.extract_text", _raise)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
        system_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system' AND hidden = 0"
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "error"
    assert len(system_messages) == 1
    assert "pdf inválido" in system_messages[0]["content"]


def test_process_next_job_agent_turn_history_includes_pdf_extract_text(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob olha esse pdf')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'pdf_extract')",
        (conversation_id, "conteúdo extraído do pdf de teste"),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "vi o pdf",
    )

    process_next_job()

    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("conteúdo extraído do pdf de teste" in c for c in contents)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_queue_worker.py -k pdf -v`
Expected: FAIL — `test_process_next_job_extract_pdf_*` falham com `AttributeError: <module 'app.queue_worker'> does not have the attribute 'extract_text'` (monkeypatch); `test_process_next_job_agent_turn_history_includes_pdf_extract_text` passa trivialmente (já é o comportamento de `_build_history` sem exclusão) — não é um problema, essa afirmação já vale hoje.

- [ ] **Step 3: Importar `extract_text` e adicionar `_process_extract_pdf` em `app/queue_worker.py`**

No topo do arquivo, junto dos outros imports de `app.*`:

```python
from app.pdf_extract import extract_text
```

Logo depois de `_process_describe_image`:

```python
PDF_NO_TEXT_WARNING = "Nenhum texto extraível encontrado neste PDF (pode ser um documento escaneado)."


def _process_extract_pdf(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    payload = json.loads(job["payload"])
    text = extract_text(payload["pdf_path"])

    content = text if text else PDF_NO_TEXT_WARNING
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'pdf_extract')",
        (job["conversation_id"], content),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))
```

- [ ] **Step 4: Adicionar o dispatch em `process_next_job`**

Em `app/queue_worker.py`, dentro de `process_next_job`, trocar:

```python
            if job["job_type"] == "describe_image":
                _process_describe_image(conn, job)
            else:
                _process_agent_turn(conn, job)
```

por:

```python
            if job["job_type"] == "describe_image":
                _process_describe_image(conn, job)
            elif job["job_type"] == "extract_pdf":
                _process_extract_pdf(conn, job)
            else:
                _process_agent_turn(conn, job)
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_queue_worker.py -v`
Expected: PASS (todos os testes de `test_queue_worker.py`, incluindo os 4 novos de PDF)

- [ ] **Step 6: Rodar a suíte completa**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS (todos os testes do projeto)

- [ ] **Step 7: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: process extract_pdf jobs in the queue worker"
```

---

### Task 5: frontend — anexar e visualizar PDF

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`

Esta task não tem testes automatizados (o projeto não tem suíte de frontend hoje — mesma situação já aceita para a busca web e o `@all`). A verificação é manual, via `preview_start`, no Step 5.

- [ ] **Step 1: Adicionar o input de arquivo em `index.html`**

Em `app/static/index.html`, logo depois do bloco do input de imagem (`<span id="image-filename" class="file-name"></span>`):

```html
<label class="file-btn" for="pdf-input">Anexar PDF</label>
<input id="pdf-input" type="file" accept="application/pdf" class="visually-hidden" />
<span id="pdf-filename" class="file-name"></span>
```

- [ ] **Step 2: Atualizar `app.js` — listener do input e lógica de submit**

Logo depois do listener de `image-input` (`document.getElementById("image-input").addEventListener(...)`), adicionar:

```javascript
document.getElementById("pdf-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  document.getElementById("pdf-filename").textContent = file ? file.name : "";
});
```

No handler `document.getElementById("message-form").onsubmit`, o bloco atual é:

```javascript
document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeConversationId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else {
    await api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
  await pollPendingStatus();
};
```

Trocar por (adiciona o ramo de PDF entre o de imagem e o de texto puro):

```javascript
document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeConversationId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");
  const pdfInput = document.getElementById("pdf-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else if (pdfInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("pdf", pdfInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/pdf`, { method: "POST", body: form });
    pdfInput.value = "";
    document.getElementById("pdf-filename").textContent = "";
  } else {
    await api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
  await pollPendingStatus();
};
```

- [ ] **Step 3: Renderizar o link do PDF na timeline**

No bloco de renderização de mensagem (logo depois de `if (message.image_path) { ... }`), adicionar:

```javascript
if (message.pdf_path) {
  const link = document.createElement("a");
  link.href = `/api/conversations/${message.conversation_id}/messages/${message.id}/pdf`;
  link.textContent = "📄 PDF anexado";
  link.target = "_blank";
  bubble.appendChild(link);
}
```

- [ ] **Step 4: Atualizar o indicador de fila pendente**

Em `renderPendingIndicator`, o bloco atual é:

```javascript
  const labels = pending.map((job) =>
    job.job_type === "describe_image"
      ? "Analisando a imagem enviada…"
      : `${job.agent_name || "agente"} está respondendo…`
  );
```

Trocar por:

```javascript
  const labels = pending.map((job) =>
    job.job_type === "describe_image"
      ? "Analisando a imagem enviada…"
      : job.job_type === "extract_pdf"
        ? "Lendo o PDF enviado…"
        : `${job.agent_name || "agente"} está respondendo…`
  );
```

- [ ] **Step 5: Verificação manual no navegador**

1. Rodar o servidor: `preview_start` com a configuração do projeto (`.claude/launch.json`, se existir) ou `F:\Projetos\boardroom\.venv\Scripts\python.exe -m uvicorn app.main:app --reload` a partir da raiz do projeto.
2. Abrir a aplicação, criar (ou usar) um grupo com pelo menos um agente membro, abrir uma conversa.
3. Clicar em "Anexar PDF", selecionar qualquer arquivo `.pdf` local pequeno, digitar um texto opcional, e enviar.
4. Confirmar visualmente: (a) a mensagem aparece na timeline com o link "📄 PDF anexado"; (b) o indicador de fila mostra "Lendo o PDF enviado…" brevemente; (c) clicar no link abre/baixa o PDF original; (d) depois de alguns segundos, mencionar `@nome-do-agente` e confirmar que a resposta do agente reflete ter "lido" o conteúdo do PDF (ou reporta que não conseguiu, se o PDF de teste não tiver texto real).
5. Checar o console do navegador (`read_console_messages` se estiver usando o Browser pane) por qualquer erro JS.

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: add PDF attachment UI"
```

---

### Task 6: atualizar a spec e rodar a suíte completa

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-pdf-reading-design.md`

- [ ] **Step 1: Rodar a suíte de testes inteira**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — todos os testes do projeto, incluindo os novos das Tasks 1-4.

- [ ] **Step 2: Atualizar o status no topo da spec**

Em `docs/superpowers/specs/2026-09-20-pdf-reading-design.md`, trocar a linha:

```
Status: Aprovado para planejamento
```

por:

```
Status: Implementado
```

- [ ] **Step 3: Commit final**

```bash
git add docs/superpowers/specs/2026-09-20-pdf-reading-design.md
git commit -m "docs: mark PDF reading spec as implemented"
```
