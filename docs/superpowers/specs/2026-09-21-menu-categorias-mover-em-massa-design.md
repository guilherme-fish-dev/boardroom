# Menu de Categorias + Mover em Massa — design

## Contexto

A feature de [categorias de agentes](2026-09-20-categorias-de-agentes-design.md) já está em produção: existe um conceito de categoria (N:N, opcional, sem cor) usado para organizar/filtrar agentes, gerenciado hoje num painel dentro da tela de Agentes (criar/apagar categoria, e um multi-select de checkboxes no formulário de cada agente).

Com mais agentes e categorias em uso, marcar categoria agente-por-agente ficou lento. É necessário um jeito de mover **vários agentes de uma vez** para dentro/fora de uma categoria — inspirado no widget de gestão de fila do Salesforce (dual-listbox: "Disponíveis" / "Selecionados", com busca e botões Adicionar/Remover).

## Fora de escopo

- Renomear categoria (permanece só criar/apagar, como no design original).
- Cor ou ícone de categoria (já descartado no design anterior).
- Qualquer mudança em `groups`/`group_members` (grupos de conversa) — a feature de categoria continua isolada.
- Mover agentes em massa entre grupos de conversa (isso é só para categorias).

## Navegação

O botão "Agentes" na barra lateral vira um cabeçalho de accordion: `👥 Agentes ▾`. Clicar nele apenas expande/recolhe a lista de subitens — não navega sozinho. Dentro, dois subitens:

- **Agentes** — a tela existente de CRUD de agente (formulário individual), agora **sem** o painel de gestão de categorias (chips + criar/apagar). O formulário de agente mantém o multi-select de categorias (checkboxes) para marcar a categoria daquele agente específico.
- **Categorias** — nova tela (ver abaixo).

Cada subitem navega para sua respectiva tela e fica visualmente destacado quando ativo (mesmo padrão de estado ativo já usado em `nav-agents`/`nav-settings`).

## Tela "Categorias" (nova)

### Criar / apagar categoria

Mesma UX que já existe hoje (só realocada): campo + botão "Criar categoria", chips das categorias existentes com botão "🗑" (apagar), pedindo confirmação via `confirm()` nativo quando a categoria tiver agentes vinculados (`agent_count > 0`), igual ao comportamento já implementado.

### Selecionar categoria ativa

Clicar num chip de categoria o marca como "categoria ativa" (destaque visual), controlando qual categoria o dual-listbox abaixo está editando. Nenhuma categoria ativa por padrão — o dual-listbox só aparece depois que uma é selecionada.

### Mover em massa (dual-listbox)

Duas colunas lado a lado:

- **Disponíveis** — agentes que NÃO pertencem à categoria ativa.
- **Nesta categoria** — agentes que pertencem à categoria ativa.

Cada coluna tem um campo de busca (filtra por nome, client-side) acima de um `<select multiple>` (seleção nativa do navegador — ctrl+clique / shift+clique para selecionar vários, sem checkboxes visuais).

Entre as duas colunas, dois botões:

- **"Adicionar →"** — move todos os agentes selecionados na coluna "Disponíveis" para a categoria ativa.
- **"← Remover"** — move todos os agentes selecionados na coluna "Nesta categoria" para fora da categoria ativa.

Cada clique dispara uma chamada de API em lote (ver abaixo) e recarrega as duas listas.

## API (backend)

Novos endpoints em `app/routers/agent_categories.py`:

- `GET /api/agent-categories/{category_id}/members` → lista `[{id, name}]` dos agentes que pertencem à categoria, seguindo o mesmo padrão de `GET /api/groups/{group_id}/members`. 404 se a categoria não existir.
- `POST /api/agent-categories/{category_id}/members/add` → body `{agent_ids: list[int]}`. Insere os vínculos em lote na tabela `agent_category_members` (idempotente — reinserir um vínculo já existente não é erro). 404 se a categoria não existir; 400 se algum `agent_id` não existir (violação de FK).
- `POST /api/agent-categories/{category_id}/members/remove` → body `{agent_ids: list[int]}`. Remove os vínculos em lote (idempotente — remover um vínculo inexistente não é erro). 404 se a categoria não existir.

Optou-se por dois endpoints `POST` com sufixo (`/add`, `/remove`) em vez de um único `DELETE` com corpo, por ser mais convencional e evitar depender de suporte a corpo em requisições `DELETE`.

Nenhuma mudança é necessária nos endpoints já existentes (`GET/POST /api/agent-categories`, `DELETE /api/agent-categories/{id}`, `category_ids` em `agents.py`) — a tela de Agentes continua usando `GET /api/agent-categories` pra popular o multi-select do formulário.

## UI — remoção do painel antigo

Em `app/static/index.html`/`app.js`, o bloco `#agent-categories-panel` (criar/listar/apagar categoria) sai de dentro de `#view-agents` e vira o conteúdo principal de uma nova view `#view-agent-categories`. As funções `renderAgentCategoryList`/`loadAgentCategories` já existentes são reaproveitadas; `renderAgentCategoryCheckboxes` continua sendo usada só pelo formulário de agente.

## Testes

- Backend: testes de API para os 3 novos endpoints (listar membros, adicionar em lote incluindo idempotência e agent_id inválido, remover em lote incluindo idempotência), seguindo o padrão dos testes já existentes em `tests/test_agent_categories_api.py`.
- Manual/UI: expandir/recolher o accordion, navegar entre Agentes/Categorias, selecionar categoria ativa, mover agentes com seleção múltipla nativa (ctrl/shift+clique) em ambas as direções, confirmar que o formulário de agente continua funcionando com o multi-select de categorias.
