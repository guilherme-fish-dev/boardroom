# Autocomplete de @menção Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ao digitar `@` no campo de mensagem, mostrar um dropdown com sugestões
dos membros da conversa atual (+ `all`) que combinam com o texto digitado,
navegável por teclado (setas, Enter/Tab, Esc) e por clique.

**Architecture:** Tudo em client-side vanilla JS (`app/static/app.js`), sem
mudança de backend — `app/mentions.py` já resolve nomes com espaço. Um `<ul>`
de sugestões é adicionado ao `#message-form` (HTML) e um pequeno CSS de
posicionamento absoluta. A lógica ouve `input`/`keydown`/`click`/`blur` no
`#message-input` existente.

**Tech Stack:** HTML/CSS/JS puro, servido estático (sem bundler, sem test
runner de frontend no repo — a verificação é manual, via browser, seguindo o
padrão do projeto).

**Spec:** [docs/superpowers/specs/2026-09-21-mention-autocomplete-design.md](../specs/2026-09-21-mention-autocomplete-design.md)

---

## Contexto para quem for implementar

- `state.members` (populado por `loadMembers`/`renderMembers` em `app.js`) é a
  lista de membros (agentes) da conversa atual — cada item tem pelo menos
  `{ id, name }`.
- O campo de mensagem é `#message-input`, dentro do `<form id="message-form">`
  em [app/static/index.html:76-85](../../../app/static/index.html).
- O envio da mensagem acontece no handler `document.getElementById("message-form").onsubmit`
  perto do final de `app.js` (por volta da linha 1359). Pressionar `Enter` num
  `<input>` dentro de um `<form>` dispara esse submit — por isso precisamos de
  um listener de `keydown` no próprio input que intercepte e cancele isso
  quando o dropdown estiver aberto.
- Não existe test runner JS no projeto (sem `package.json`, sem Jest). A
  verificação desta feature é manual: rodar o app localmente e testar no
  browser (preview tool), como já é prática no projeto para mudanças de
  frontend.
- `app/static/style.css` usa variáveis de tema como `--surface`, `--border`,
  `--ink-muted`, `--radius-sm`, `--bg-elevated` (ver bloco `:root` no topo do
  arquivo) — reaproveitar essas variáveis para o dropdown ficar consistente
  com o resto da UI.

---

### Task 1: Estrutura HTML e CSS do dropdown

**Files:**
- Modify: `app/static/index.html:76-85` (dentro de `#message-form`)
- Modify: `app/static/style.css` (perto da regra `#message-form` / `#message-input`, linhas 456-457)

- [ ] **Step 1: Adicionar o `<ul>` de sugestões no HTML**

Em `app/static/index.html`, dentro de `<form id="message-form">`, logo antes
do `<input id="message-input" .../>`, envolver o input numa div wrapper para
posicionamento relativo e adicionar a lista de sugestões logo depois do input:

```html
            <form id="message-form">
              <div id="message-input-wrapper">
                <input id="message-input" placeholder="Mensagem (@nome ou @all para mencionar)" autocomplete="off" />
                <ul id="mention-suggestions" class="hidden"></ul>
              </div>
              <label class="file-btn" for="image-input">Anexar imagem</label>
```

(O restante do form — `image-input`, `pdf-input`, botão "Enviar" — continua
igual, apenas fora da nova `div`.)

- [ ] **Step 2: Adicionar CSS do wrapper e do dropdown**

Em `app/static/style.css`, substituir a regra existente:

```css
#message-form { display: flex; flex-direction: row; gap: 8px; align-items: center; }
#message-input { flex: 1; }
```

por:

```css
#message-form { display: flex; flex-direction: row; gap: 8px; align-items: center; }
#message-input-wrapper { position: relative; flex: 1; }
#message-input { width: 100%; }

#mention-suggestions {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 220px;
  overflow-y: auto;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  z-index: 20;
}
#mention-suggestions.hidden { display: none; }
#mention-suggestions li {
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--ink-muted);
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
#mention-suggestions li.active,
#mention-suggestions li:hover {
  background: var(--surface);
  color: var(--ink);
}
```

(Se `var(--ink)` não existir no arquivo, checar o bloco `:root` no topo de
`style.css` e usar a variável de texto principal equivalente já usada por
outros elementos, como `.member-badge`.)

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html app/static/style.css
git commit -m "feat: adiciona estrutura e estilo do dropdown de menção"
```

---

### Task 2: Lógica de detecção e filtro de candidatos

**Files:**
- Modify: `app/static/app.js` (novo bloco de funções, antes do handler
  `document.getElementById("message-form").onsubmit` por volta da linha 1359)

- [ ] **Step 1: Adicionar estado do autocomplete**

Em `app.js`, no objeto `state` (topo do arquivo, onde já está
`agentMentionSettings: {}`), adicionar:

```javascript
const state = {
  // ...campos existentes...
  agentMentionSettings: {},
  mention: { active: false, start: -1, end: -1, activeIndex: 0, candidates: [] },
  // ...restante existente...
};
```

- [ ] **Step 2: Implementar `getMentionCandidates(query)`**

Adicionar logo antes do bloco `document.getElementById("message-form").onsubmit`:

```javascript
function getMentionCandidates(query) {
  const lowerQuery = query.toLowerCase();
  const candidates = [];
  if ("all".startsWith(lowerQuery)) {
    candidates.push({ id: "all", name: "all", isAll: true });
  }
  for (const member of state.members) {
    if (member.name.toLowerCase().startsWith(lowerQuery)) {
      candidates.push({ id: member.id, name: member.name, isAll: false });
    }
  }
  return candidates;
}
```

- [ ] **Step 3: Verificação manual da função pura**

Não há test runner JS no projeto. Verificar a lógica abrindo o devtools do
browser (preview tool) e rodando no console, após o app carregar:

```javascript
getMentionCandidates("a")
```

Esperado: array contendo `{id: "all", name: "all", isAll: true}` seguido de
qualquer membro cujo nome comece com "a" (case-insensitive). Confirmar visualmente
o resultado no console antes de seguir.

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "feat: adiciona filtro de candidatos de menção"
```

---

### Task 3: Renderização do dropdown e seleção

**Files:**
- Modify: `app/static/app.js` (mesmo bloco da Task 2)

- [ ] **Step 1: Implementar `renderMentionSuggestions()` e `closeMentionSuggestions()`**

```javascript
function renderMentionSuggestions() {
  const list = document.getElementById("mention-suggestions");
  list.innerHTML = "";
  if (!state.mention.active || state.mention.candidates.length === 0) {
    list.classList.add("hidden");
    return;
  }
  state.mention.candidates.forEach((candidate, index) => {
    const li = document.createElement("li");
    li.textContent = candidate.isAll ? "all — mencionar todos os agentes" : candidate.name;
    if (index === state.mention.activeIndex) li.classList.add("active");
    li.onclick = () => applyMentionCandidate(candidate);
    list.appendChild(li);
  });
  list.classList.remove("hidden");
}

function closeMentionSuggestions() {
  state.mention = { active: false, start: -1, end: -1, activeIndex: 0, candidates: [] };
  renderMentionSuggestions();
}
```

- [ ] **Step 2: Implementar `applyMentionCandidate(candidate)`**

```javascript
function applyMentionCandidate(candidate) {
  const input = document.getElementById("message-input");
  const { start, end } = state.mention;
  const before = input.value.slice(0, start);
  const after = input.value.slice(end);
  const inserted = `@${candidate.name} `;
  input.value = before + inserted + after;
  const cursor = before.length + inserted.length;
  input.focus();
  input.setSelectionRange(cursor, cursor);
  closeMentionSuggestions();
}
```

- [ ] **Step 3: Implementar `updateMentionState()` — detecção da menção ativa**

```javascript
function updateMentionState() {
  const input = document.getElementById("message-input");
  const cursor = input.selectionStart;
  const value = input.value;
  const atIndex = value.lastIndexOf("@", cursor - 1);
  if (atIndex === -1) {
    closeMentionSuggestions();
    return;
  }
  const query = value.slice(atIndex + 1, cursor);
  if (query.includes("@") || query.includes("\n")) {
    closeMentionSuggestions();
    return;
  }
  const candidates = getMentionCandidates(query);
  if (candidates.length === 0) {
    closeMentionSuggestions();
    return;
  }
  state.mention = {
    active: true,
    start: atIndex,
    end: cursor,
    activeIndex: 0,
    candidates,
  };
  renderMentionSuggestions();
}
```

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "feat: adiciona renderização e seleção do dropdown de menção"
```

---

### Task 4: Ligar os event listeners no `#message-input`

**Files:**
- Modify: `app/static/app.js` (logo antes de
  `document.getElementById("message-form").onsubmit`)

- [ ] **Step 1: Adicionar os listeners**

```javascript
const mentionInputEl = document.getElementById("message-input");

mentionInputEl.addEventListener("input", () => {
  updateMentionState();
});

mentionInputEl.addEventListener("keydown", (e) => {
  if (!state.mention.active) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    state.mention.activeIndex = (state.mention.activeIndex + 1) % state.mention.candidates.length;
    renderMentionSuggestions();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    state.mention.activeIndex =
      (state.mention.activeIndex - 1 + state.mention.candidates.length) % state.mention.candidates.length;
    renderMentionSuggestions();
  } else if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    applyMentionCandidate(state.mention.candidates[state.mention.activeIndex]);
  } else if (e.key === "Escape") {
    e.preventDefault();
    closeMentionSuggestions();
  }
});

mentionInputEl.addEventListener("keyup", (e) => {
  if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
    updateMentionState();
  }
});

mentionInputEl.addEventListener("blur", () => {
  // Pequeno atraso para permitir que o clique num item do dropdown (que também
  // dispara blur) seja processado antes de fechar a lista.
  setTimeout(() => closeMentionSuggestions(), 150);
});
```

**Nota:** este bloco deve ficar depois das definições de
`updateMentionState`, `renderMentionSuggestions`, `applyMentionCandidate` e
`closeMentionSuggestions` (Task 3) e antes do handler de submit do form —
mantendo a ordem de leitura do arquivo (definições de função antes do uso no
event listener não é estritamente necessário em JS por hoisting de function
declarations, mas mantenha os blocos próximos para legibilidade).

- [ ] **Step 2: Commit**

```bash
git add app/static/app.js
git commit -m "feat: liga listeners do autocomplete de menção ao campo de mensagem"
```

---

### Task 5: Verificação manual completa no browser

**Files:** nenhum (apenas verificação)

- [ ] **Step 1: Rodar o app localmente**

Usar o preview tool do projeto (ou `start.bat`) para subir o servidor local e
abrir o app no browser numa conversa que já tenha pelo menos 2 agentes
membros, um deles com nome composto (ex. "Osvaldo Tibúrcio") se existir na
base de dados de teste, ou criar um agente assim para o teste.

- [ ] **Step 2: Testar o fluxo básico**

No campo de mensagem, digitar `@` seguido de uma letra que combine com pelo
menos um membro. Confirmar:
- O dropdown aparece logo acima do campo.
- Os candidatos exibidos combinam com o texto digitado (incluindo `all`
  quando aplicável).
- `ArrowDown`/`ArrowUp` movem o destaque.
- `Enter` insere `@Nome ` no campo (com o nome completo, mesmo se composto),
  fecha o dropdown e **não envia a mensagem**.
- Clicar num item da lista tem o mesmo efeito do `Enter`.
- `Escape` fecha o dropdown sem alterar o texto.

- [ ] **Step 3: Testar o caso de nome composto**

Digitar `@` seguido do início do primeiro nome de um agente composto (ex.
`@osvaldo`), depois continuar digitando um espaço e o início do sobrenome
(ex. `@osvaldo tib`). Confirmar que o dropdown continua mostrando esse agente
como candidato enquanto o texto ainda é um prefixo válido do nome completo, e
que confirmar a seleção insere o nome completo corretamente.

- [ ] **Step 4: Testar o caso "sem candidatos"**

Digitar `@xyz123` (texto que não combina com nenhum nome nem com `all`).
Confirmar que o dropdown fecha/não aparece.

- [ ] **Step 5: Testar envio normal sem menção**

Digitar uma mensagem sem `@` e confirmar que `Enter` continua enviando a
mensagem normalmente (regressão do fluxo existente).

- [ ] **Step 6: Capturar evidência**

Tirar um screenshot do dropdown aberto (via preview tool) para confirmar
visualmente o resultado antes de considerar a task concluída.
