# start.bat / stop.bat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `start.bat` and `stop.bat` scripts at the project root so the user can launch the Boardroom server in the background (with the browser opening automatically) and stop it later, without manually managing a terminal or a Python process.

**Architecture:** Two standalone Windows batch scripts. `start.bat` uses an inline PowerShell one-liner (`Start-Process -PassThru`) to launch `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` as a hidden, detached process, capturing its PID into `boardroom.pid` and redirecting output to `boardroom.log`/`boardroom.err.log`. `stop.bat` reads that PID and kills the process tree with `taskkill`. No Python code changes — this is pure operational tooling around the existing server.

**Tech Stack:** Windows `cmd` batch scripting + inline PowerShell (`Start-Process`, `tasklist`) for process control. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-17-start-stop-scripts-design.md`

---

## File Structure

```
boardroom/
  start.bat   # new: launch server in background, open browser
  stop.bat    # new: stop the background server
  .gitignore  # already updated: boardroom.pid, boardroom.log, boardroom.err.log
```

No test suite applies to batch scripts (no pytest equivalent for `.bat` in this project). Each task's verification step is a manual command sequence with an exact expected outcome, run by the engineer implementing it.

---

### Task 1: `start.bat`

**Files:**
- Create: `start.bat` (project root, `F:\Projetos\boardroom\start.bat`)

- [ ] **Step 1: Write `start.bat`**

```bat
@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [Boardroom] .venv nao encontrado. Rode primeiro:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    exit /b 1
)

if exist "boardroom.pid" (
    set /p OLDPID=<boardroom.pid
    tasklist /FI "PID eq !OLDPID!" 2>NUL | find /I "!OLDPID!" >NUL
    if not errorlevel 1 (
        echo [Boardroom] Ja esta em execucao ^(PID !OLDPID!^). Rode stop.bat primeiro se quiser reiniciar.
        exit /b 1
    ) else (
        del "boardroom.pid"
    )
)

for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command ^
    "(Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--port','8000' -WindowStyle Hidden -RedirectStandardOutput 'boardroom.log' -RedirectStandardError 'boardroom.err.log' -PassThru).Id"`) do (
    set PID=%%p
)

if "!PID!"=="" (
    echo [Boardroom] Falha ao iniciar o servidor. Veja boardroom.err.log.
    exit /b 1
)

echo !PID!> boardroom.pid
echo [Boardroom] Iniciado ^(PID !PID!^). Logs em boardroom.log / boardroom.err.log.

timeout /t 2 /nobreak >nul
start "" http://localhost:8000
```

- [ ] **Step 2: Verificação manual — servidor não estava rodando**

Pré-requisito: `.venv` já criado e dependências instaladas (`pip install -r requirements.txt`), nenhum `boardroom.pid` presente.

Run: `start.bat` (duplo-clique no Explorer, ou `cmd /c start.bat` no terminal)

Expected:
- Mensagem `[Boardroom] Iniciado (PID <numero>).` aparece no terminal.
- Arquivo `boardroom.pid` existe na raiz do projeto e contém um número.
- O navegador padrão abre automaticamente em `http://localhost:8000` mostrando a interface do Boardroom.
- `curl -s http://localhost:8000/` (em outro terminal) retorna HTML começando com `<!DOCTYPE html>`.

- [ ] **Step 3: Verificação manual — rodar de novo enquanto já está no ar**

Run: `start.bat` de novo, com o processo do passo anterior ainda rodando.

Expected: mensagem `[Boardroom] Ja esta em execucao (PID <mesmo numero do passo 2>).` e o script sai sem iniciar um segundo processo (confirme com `tasklist /FI "IMAGENAME eq python.exe"` que só há um processo novo, não dois).

- [ ] **Step 4: Verificação manual — pid file órfão**

Pare manualmente o processo iniciado no passo 2 (ex.: `taskkill /PID <pid> /T /F`), mas **não** apague `boardroom.pid` (simulando uma queda anterior que não limpou o arquivo).

Run: `start.bat`

Expected: o script detecta que o PID salvo não corresponde a nenhum processo vivo, remove o `boardroom.pid` antigo silenciosamente, e inicia um novo processo normalmente (nova mensagem `[Boardroom] Iniciado (PID <novo numero>).`).

Ao final deste passo, pare o servidor (`taskkill /PID <novo PID> /T /F` e apague `boardroom.pid` manualmente) antes de seguir para o Task 2, para começar com estado limpo.

- [ ] **Step 5: Verificação manual — sem `.venv`**

Renomeie temporariamente `.venv` para `.venv_bak` (ou rode isso numa cópia limpa do repo sem `.venv`).

Run: `start.bat`

Expected: mensagem `[Boardroom] .venv nao encontrado. Rode primeiro: ...` com as 3 linhas de instrução, e o script sai com código de erro (`exit /b 1`), sem tentar rodar Python. Depois, renomeie `.venv_bak` de volta para `.venv`.

- [ ] **Step 6: Commit**

```bash
git add start.bat
git commit -m "feat: add start.bat to launch the server in the background"
```

---

### Task 2: `stop.bat`

**Files:**
- Create: `stop.bat` (project root, `F:\Projetos\boardroom\stop.bat`)

- [ ] **Step 1: Write `stop.bat`**

```bat
@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "boardroom.pid" (
    echo [Boardroom] Nao parece estar em execucao ^(boardroom.pid nao encontrado^).
    exit /b 0
)

set /p PID=<boardroom.pid
taskkill /PID !PID! /T /F >nul 2>&1

del "boardroom.pid"
echo [Boardroom] Parado ^(PID !PID!^).
```

- [ ] **Step 2: Verificação manual — parar servidor em execução**

Pré-requisito: rode `start.bat` primeiro (ver Task 1, Step 2) pra ter um servidor de verdade no ar com `boardroom.pid` válido.

Run: `stop.bat`

Expected:
- Mensagem `[Boardroom] Parado (PID <numero>).`
- `boardroom.pid` não existe mais na raiz do projeto.
- `tasklist /FI "PID eq <numero>"` não lista mais nenhum processo com aquele PID.
- `curl -s http://localhost:8000/` (em outro terminal) falha (connection refused), confirmando que o servidor parou de fato.

- [ ] **Step 3: Verificação manual — parar sem nada rodando**

Pré-requisito: nenhum `boardroom.pid` presente (rode `stop.bat` de novo logo após o Step 2, ou apague o arquivo manualmente se ele não tiver sido removido por algum motivo).

Run: `stop.bat`

Expected: mensagem `[Boardroom] Nao parece estar em execucao (boardroom.pid nao encontrado).`, o script sai com código 0 (sucesso, não é um erro), sem travar nem lançar exceção.

- [ ] **Step 4: Verificação manual — pid file aponta pra processo já morto**

Crie manualmente um `boardroom.pid` com um número de PID que certamente não existe (ex.: `echo 999999> boardroom.pid`).

Run: `stop.bat`

Expected: `taskkill` falha silenciosamente (nenhuma mensagem de erro visível, porque a saída é suprimida com `>nul 2>&1`), mas o script ainda imprime `[Boardroom] Parado (PID 999999).` e remove o `boardroom.pid` — ou seja, o script sempre termina em estado limpo (sem pid file) independente do processo estar vivo ou não, conforme a spec.

- [ ] **Step 5: Commit**

```bash
git add stop.bat
git commit -m "feat: add stop.bat to stop the background server"
```

---

## Self-Review Notes

- **Spec coverage:** todos os pontos da spec (`docs/superpowers/specs/2026-09-17-start-stop-scripts-design.md`) — detecção de pid file órfão, verificação de `.venv`, redirecionamento de log, abertura do navegador, `stop.bat` sempre terminando limpo — estão cobertos pelas verificações manuais acima.
- **`.gitignore`:** `boardroom.pid`, `boardroom.log`, `boardroom.err.log` já foram adicionados ao `.gitignore` durante a etapa de brainstorming (commit `66c9bfe`); nenhuma ação adicional necessária nesta implementação.
- **Ordem de execução:** Task 1 antes da Task 2 é obrigatório na prática (pra testar `stop.bat` de verdade é preciso ter um servidor rodando via `start.bat`), mas os dois arquivos são independentes um do outro em termos de código — nenhum importa ou depende do outro.
