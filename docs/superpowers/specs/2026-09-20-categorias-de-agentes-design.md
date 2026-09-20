# Categorias de agentes — design

## Contexto

O Boardroom já possui "grupos" (`groups` / `group_members`), que são **grupos de conversa** (ex: "Presidência", "Finanças") — cada grupo tem várias conversas e uma lista de agentes membros que participam dessas conversas.

Com o crescimento do número de agentes, ficou difícil localizar rapidamente o agente certo ao montar um grupo de conversa (dropdown "+ adicionar agente"). É necessário um conceito **novo e independente** de "categoria de agente": um rótulo usado apenas para organizar e filtrar agentes, sem qualquer relação com os grupos de conversa existentes. Um agente pode pertencer a várias categorias; uma categoria pode não ter nenhum agente.

## Fora de escopo

- Qualquer alteração nas tabelas `groups` / `group_members` ou no conceito de grupo de conversa.
- Cor, ícone ou qualquer personalização visual de categoria — categoria é apenas um nome (nome único).
- Categoria obrigatória — um agente pode não ter nenhuma categoria.

## Modelo de dados

Duas tabelas novas em `app/db.py`, isoladas do restante do schema:

```sql
CREATE TABLE agent_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE agent_category_members (
    category_id INTEGER NOT NULL REFERENCES agent_categories(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    PRIMARY KEY (category_id, agent_id)
);
```

- `ON DELETE CASCADE` garante que apagar uma categoria remove os vínculos automaticamente, sem afetar o agente em si.
- `ON DELETE CASCADE` em `agent_id` garante que apagar um agente remove seus vínculos de categoria (mesmo padrão que provavelmente já existe implicitamente para `group_members` — verificar e replicar).

## API

Novo router `app/routers/agent_categories.py`, seguindo o padrão de `app/routers/groups.py`:

- `GET /api/agent-categories` — lista todas as categorias. Cada item inclui `agent_count` (quantidade de agentes vinculados), usado para a mensagem de confirmação de exclusão.
- `POST /api/agent-categories` — cria uma categoria (`{name: str}`). Nome único (erro 409 se duplicado).
- `DELETE /api/agent-categories/{id}` — apaga a categoria. Cascade remove os vínculos em `agent_category_members`. Sem soft-delete.

Alterações em `app/routers/agents.py`:

- `AgentIn`/payload de `POST /api/agents` e `PUT /api/agents/{id}` passam a aceitar `category_ids: list[int] = []`. Ao salvar, a rota sincroniza `agent_category_members` para aquele agente dentro de uma transação (deleta os vínculos antigos, insere os novos).
- `AgentOut`/resposta de `GET /api/agents` e `GET /api/agents/{id}` passa a incluir `category_ids: list[int]` (ids das categorias do agente), para a UI pré-marcar o multi-select e para o filtro do dropdown de grupo.

## UI — Tela de Agentes (`app/static/index.html`, `app/static/app.js`)

### Gestão de categorias

Novo bloco "Categorias" na view `#view-agents`, próximo à lista de agentes:

- Lista as categorias existentes como chips/tags, cada uma com um botão "×" para apagar.
- Campo de texto + botão "Criar categoria" para adicionar uma nova.
- Ao clicar em "×": se `agent_count > 0`, exibe `confirm()` nativo com a mensagem "Esta categoria tem N agente(s). Apagar mesmo assim?" antes de chamar `DELETE`. Se `agent_count === 0`, apaga direto sem confirmação.

### Atribuição no formulário do agente

No formulário inline de criar/editar agente (`index.html:116-143`), novo campo multi-select (checkboxes) listando todas as categorias existentes. Ao editar um agente (`startEditingAgent`), os checkboxes correspondentes às `category_ids` do agente vêm pré-marcados. Ao salvar (criar ou editar), os `category_ids` selecionados são enviados junto no mesmo payload do `POST`/`PUT`.

## UI — Filtro no dropdown de grupo de conversa (`app.js:319-372`, `renderMembers`)

Acima do `<select id="add-member-select">` existente, novo `<select id="member-category-filter">` com a opção "Todas as categorias" seguida da lista de categorias.

- Ao mudar a seleção, a lista de opções do `add-member-select` é refiltrada **em memória** (os agentes já vêm com `category_ids` do `GET /api/agents`, sem chamada extra de API), mostrando apenas agentes daquela categoria que ainda não são membros do grupo.
- Não altera o comportamento de adicionar membro em si (`onchange` do `add-member-select` continua chamando `POST /api/groups/{id}/members` normalmente).

## Testes

- Backend: testes de API para `agent_categories` (criar, listar com `agent_count`, apagar com cascade) e para a sincronização de `category_ids` em `POST`/`PUT /api/agents`.
- Manual/UI: criar categoria, atribuir a um agente pelo formulário, filtrar o dropdown de um grupo por essa categoria, apagar a categoria com agente vinculado (confirmar aviso) e sem agente vinculado (sem aviso).
