# Leitura de PDF pelos agentes

Data: 2026-09-20
Status: Aprovado para planejamento

## Contexto e motivação

Os agentes já conseguem "ver" imagens enviadas no chat: `POST .../messages/image` salva o arquivo, cria um job `describe_image` que gera uma descrição textual (para agentes sem visão) ou passa a imagem crua ao modelo (para agentes `vision_capable`). O usuário quer o mesmo tipo de capacidade para PDFs — poder anexar um documento (contrato, relatório, artigo) e ter os agentes lendo o conteúdo.

Diferente da imagem, aqui não há distinção de "modelo com/sem capacidade especial": a abordagem escolhida é extração de texto (não rasterização de página + visão), então todo agente recebe o mesmo conteúdo textual extraído, sem ramificação por `vision_capable`.

## Objetivo

- Permitir anexar um PDF a uma mensagem, do mesmo jeito que já se anexa uma imagem.
- Extrair o texto do PDF de forma assíncrona (job de fila, como `describe_image`) e disponibilizá-lo no histórico de todos os agentes do grupo, como mensagem oculta.
- PDFs sem texto extraível (ex.: documento escaneado, só imagem) geram um aviso no histórico em vez de silêncio total.
- Truncar textos muito longos para não estourar o contexto do modelo em turnos futuros.

Fora de escopo: OCR de PDFs escaneados; rasterização de páginas para modelos de visão; extração de tabelas/imagens embutidas no PDF; suporte a anexar imagem E PDF na mesma mensagem (mantém o padrão atual de um anexo por mensagem); PDFs protegidos por senha (tratados como arquivo inválido — ver seção de erros).

## Dependência nova: `pypdf`

Adicionar `pypdf` ao `requirements.txt` (biblioteca pura Python, sem dependências binárias nativas — mesma filosofia de manter o projeto leve, análogo a como `httpx` já é usado para a busca web).

## Módulo de extração: `app/pdf_extract.py`

Novo módulo, isolado (mesmo padrão de `app/web_search.py`):

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

    Raises whatever pypdf raises for a corrupt, invalid, or password-protected file
    (e.g. PdfReadError) — not caught here, propagates to the caller."""
    reader = PdfReader(pdf_path)
    pages_text = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages_text).strip()
    if len(full_text) > max_chars:
        return full_text[:max_chars] + TRUNCATION_NOTICE
    return full_text
```

Arquivos corrompidos/inválidos/protegidos por senha: a exceção do `pypdf` propaga sem tratamento — o chamador (`_process_extract_pdf`) não precisa de lógica extra, porque o `process_next_job` já tem tratamento genérico de exceção (job vira `error`, mensagem de sistema com o texto do erro) — o mesmo caminho que já existe hoje para qualquer outra falha de job.

## Schema: `pdf_path` em `messages` e novo `job_type`

Duas mudanças de schema em `app/db.py`:

**1. Nova coluna `pdf_path TEXT`** na tabela `messages` (`SCHEMA`, para bancos novos) + migração idempotente para bancos existentes, seguindo exatamente o padrão de `_ensure_hidden_kind_column`:

```python
def _ensure_pdf_path_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "pdf_path" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN pdf_path TEXT")
```

**2. `queue_jobs.job_type` precisa aceitar `'extract_pdf'`** — hoje o `CHECK` da coluna só permite `('agent_turn','describe_image')`. SQLite não permite `ALTER TABLE` para modificar um `CHECK` existente, então a migração precisa reconstruir a tabela — o projeto já tem esse padrão em `_migrate_messages_to_conversation_id`:

```python
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

A `SCHEMA` (definição para bancos novos) também é atualizada para já incluir `'extract_pdf'` no `CHECK`.

Ambas as funções são chamadas em `init_db()`, junto das migrações já existentes (`_ensure_hidden_kind_column`, `_ensure_group_icon_column`, etc.), antes de `_recover_orphaned_processing_jobs`.

## Endpoint de upload: `POST .../messages/pdf`

Em `app/routers/messages.py`, espelhando `post_image_message` (linha 261) quase linha a linha:

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

Diferenças propositais em relação ao endpoint de imagem: (1) validação de `content_type` é igualdade exata (`== "application/pdf"`), não prefixo, porque MIME de PDF não tem variação de subtipo como imagem; (2) extensão do arquivo salvo é sempre `.pdf` fixo (não deriva do nome original), já que não há ambiguidade de formato como em imagem (`.png`/`.jpg`/etc.).

`MessageOut` e `_row_to_message` (topo de `messages.py`) ganham o campo `pdf_path: str | None`, espelhando `image_path`.

## Job `extract_pdf`: `app/queue_worker.py`

Nova função `_process_extract_pdf`, ao lado de `_process_describe_image`:

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

Em `process_next_job` (`app/queue_worker.py:358`), o dispatch ganha o terceiro ramo:

```python
if job["job_type"] == "describe_image":
    _process_describe_image(conn, job)
elif job["job_type"] == "extract_pdf":
    _process_extract_pdf(conn, job)
else:
    _process_agent_turn(conn, job)
```

Nenhuma mudança em `_build_history`: a mensagem oculta com `hidden_kind='pdf_extract'` entra no histórico de todo agente normalmente (sem filtro condicional, ao contrário de `image_description`), porque não existe um "PDF cru" alternativo que a duplicaria — não há ramificação por `vision_capable` para PDF.

## Frontend: `app/static/index.html` e `app.js`

Em `index.html`, ao lado do input de imagem (linha 72-74):

```html
<label class="file-btn" for="pdf-input">Anexar PDF</label>
<input id="pdf-input" type="file" accept="application/pdf" class="visually-hidden" />
<span id="pdf-filename" class="file-name"></span>
```

Em `app.js`, ao lado do listener de `image-input` (linha 920):

```javascript
document.getElementById("pdf-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  document.getElementById("pdf-filename").textContent = file ? file.name : "";
});
```

No handler de submit do formulário (`app.js:925`), a lógica de decidir qual endpoint chamar ganha um terceiro ramo (imagem, PDF, ou texto puro — mutuamente exclusivos, seguindo a decisão de "um anexo por mensagem"):

```javascript
const pdfInput = document.getElementById("pdf-input");

if (imageInput.files.length > 0) {
  // ... fluxo de imagem já existente, inalterado
} else if (pdfInput.files.length > 0) {
  const form = new FormData();
  form.append("content", textInput.value);
  form.append("pdf", pdfInput.files[0]);
  await api(`/api/conversations/${state.activeConversationId}/messages/pdf`, { method: "POST", body: form });
  pdfInput.value = "";
  document.getElementById("pdf-filename").textContent = "";
} else {
  // ... fluxo de texto puro já existente, inalterado
}
```

Na renderização de mensagens (`app.js:488`, ao lado do `if (message.image_path)`):

```javascript
if (message.pdf_path) {
  const link = document.createElement("a");
  link.href = `/api/conversations/${message.conversation_id}/messages/${message.id}/pdf`;
  link.textContent = "📄 PDF anexado";
  link.target = "_blank";
  bubble.appendChild(link);
}
```

No indicador de fila pendente (`app.js:570`, `renderPendingIndicator`):

```javascript
const labels = pending.map((job) =>
  job.job_type === "describe_image"
    ? "Analisando a imagem enviada…"
    : job.job_type === "extract_pdf"
      ? "Lendo o PDF enviado…"
      : `${job.agent_name || "agente"} está respondendo…`
);
```

## Testes

Backend:

`tests/test_pdf_extract.py` (novo, análogo a `test_web_search.py`): `extract_text()` com PDFs de teste gerados em memória/fixture (`pypdf.PdfWriter` para criar um PDF mínimo com texto conhecido) — extração de texto simples de uma página; concatenação de múltiplas páginas; truncamento quando o texto excede `max_chars` (usar um `max_chars` pequeno no teste, não o padrão de 20k, pra não precisar gerar um PDF gigante); PDF sem texto (página em branco) retorna string vazia; arquivo corrompido (bytes aleatórios com extensão `.pdf`) propaga exceção do `pypdf`.

`tests/test_db.py`: `_ensure_pdf_path_column` idempotente (rodar `init_db()` duas vezes não quebra; coluna existe depois); `_ensure_queue_jobs_allows_extract_pdf` idempotente e preserva jobs já existentes na tabela (inserir um job `agent_turn` antes da migração, rodar a migração, confirmar que o job continua lá com os mesmos dados); inserir um job com `job_type='extract_pdf'` funciona depois da migração (não dispara mais o `CHECK` antigo).

`tests/test_messages_api.py`: `POST .../messages/pdf` com um PDF válido cria mensagem com `pdf_path` preenchido e um job `extract_pdf`; rejeita `content_type` que não seja `application/pdf` (415); rejeita arquivo maior que `MAX_PDF_SIZE_BYTES` (413); 404 para conversa inexistente; `GET .../messages/{id}/pdf` retorna os bytes do arquivo salvo; 404 quando a mensagem não tem PDF.

`tests/test_queue_worker.py`: `_process_extract_pdf` com `extract_text` mockado retornando um texto normal — mensagem oculta criada com `hidden_kind='pdf_extract'` e o texto exato, job termina `done`; `extract_text` mockado retornando string vazia — mensagem oculta criada com o texto de `PDF_NO_TEXT_WARNING`; `extract_text` mockado levantando exceção — job termina `error` e mensagem de sistema visível é criada com o erro (mesmo comportamento genérico já testado para outras falhas de job, ex. `test_process_next_job_rolls_back_partial_work_on_error`); texto extraído entra no histórico de um agente comum via `_build_history` (sem exclusão, ao contrário de `image_description`).

Sem teste de frontend automatizado (o projeto não tem suíte de frontend hoje — mesma situação da feature de busca web e do `@all`); verificação manual via `preview_start` cobre a UI.
