# Redesign visual do Boardroom — spec

Data: 2026-09-18
Status: aprovado, aguardando plano de implementação

## Contexto

O usuário trouxe um mockup de referência para o Boardroom (screenshot de um app
similar) propondo um layout mais elaborado: sidebar de dois níveis (grupos +
conversas), painel de contexto lateral direito, tema mais escuro/laranja, e
ícones por grupo. O mockup completo inclui várias funcionalidades que não
existem hoje no backend (arquivos por conversa, execução de código, conversas
relacionadas, fixar/arquivar conversa, like/dislike/regenerar mensagem,
"agente principal"). Depois de conversar sobre escopo, esta rodada fica
restrita à parte visual/estrutural com os dados que já existem, mais uma
pequena adição de schema (ícone do grupo). As funcionalidades novas maiores
ficam registradas como fora de escopo para uma rodada futura.

## Objetivo

Reorganizar a UI do Boardroom em 4 colunas (grupos, conversas do grupo,
chat, painel de contexto), com tema visual mais próximo do mockup, mantendo
o comportamento funcional atual (nenhuma regra de negócio nova, exceto o
ícone de grupo).

## Estrutura de layout

**Coluna 1 — Grupos** (substitui `#sidebar` atual)
- Lista vertical de grupos, cada um com ícone emoji + nome.
- Campo de busca client-side (filtra a lista por substring do nome, sem
  chamada ao backend).
- Nav (Início/Agentes/Configurações) no topo, como hoje.
- Botão de colapsar esta coluna (fica só com os ícones dos grupos, sem nome).
- Criar grupo abre um formulário (ver seção "Ícone de grupo") em vez do
  formulário inline atual.

**Coluna 2 — Conversas do grupo ativo** (substitui `#conversation-tabs`)
- Lista vertical de cards, um por conversa (nome + data de criação),
  em vez das abas horizontais de hoje.
- Botão "+ Nova conversa" destacado no topo da coluna.
- Cada card mantém ✎ renomear e × apagar (mesma lógica/confirmação de hoje).
- Botão de colapsar esta coluna, independente da coluna 1.
- Quando nenhum grupo está selecionado, a coluna fica vazia/oculta (estado
  atual de `#channel-empty` se aplica à área de chat, não a esta coluna).

**Coluna central — Chat**
- Header com nome do grupo, renomear/apagar grupo (como hoje).
- Indicador de fila (`#queue-indicator`), lista de mensagens, formulário de
  envio — mesma estrutura/lógica de hoje, sem botões de ação nas mensagens.
- A lista de membros (`#member-list` + `#add-member-select`) sai de cima do
  chat e passa a viver na coluna 4.

**Coluna 4 — Painel de contexto** (novo)
- "Contexto da conversa": nome da conversa ativa + data de criação.
- "Membros": a lista de membros + seletor de adicionar agente (mesma lógica
  de `loadMembers`/`renderMembers`, só realocada).
- "Ferramentas ativas": card estático, não interativo, listando "🌐 Busca na
  Web" — a única capacidade real hoje (ver `WEB_SEARCH_INSTRUCTIONS` em
  `app/queue_worker.py`). Não reflete estado por conversa, é sempre exibido.
- Botão de colapsar esta coluna, independente das colunas 1 e 2.
- Sem card de "agente principal", sem arquivos, sem conversas relacionadas.

## Ícone de grupo

- Nova coluna `icon` em `groups`: `TEXT NOT NULL DEFAULT '💬'`.
- Migração aditiva em `app/db.py`, mesmo padrão de `_ensure_hidden_kind_column`
  (checa `PRAGMA table_info(groups)`, adiciona a coluna se faltar).
- `GroupIn`/`GroupOut` (`app/routers/groups.py`) passam a incluir `icon`
  (opcional em `GroupIn`, com default `'💬'` se omitido).
- Criar/renomear grupo deixa de usar `prompt()` do navegador (não dá pra
  escolher emoji num prompt) e passa a abrir um formulário próprio — inline
  ou popover simples — com campo de nome + uma grade de emojis pré-definidos
  (conjunto fixo de ~12–16 opções, cobrindo os temas do mockup: 📊 📁 🧑 📚
  🎨 🗑️ 💬 💰 ⚙️ 🧪 🚀 📈, entre outros). Não é um emoji picker livre —
  é uma grade fixa, sem busca ou paleta completa de unicode.
- O grupo apagado continua indo pra lixeira lógica? Não — hoje `delete_group`
  já apaga de fato (`DELETE FROM groups`), isso não muda nesta spec.

## Visual

- Mantém os tokens `oklch` já existentes em `app/static/style.css` (já é
  dark theme). Ajusta `--hue` e a saturação do acento (`--accent`,
  `--accent-hover`, `--accent-active`) pra ficar mais próximo do laranja do
  mockup.
- Cards com raio (`--radius-md`) e borda sutil nos itens de grupo, conversa
  e nos blocos do painel de contexto, em vez das listas simples de hoje.

## Responsivo

- Breakpoint mobile (`max-width: 760px`, já existente) passa a colapsar as
  colunas 1, 2 e 4 por padrão, cada uma abrindo como overlay via seu próprio
  toggle — nunca as 4 colunas simultâneas na tela. Reaproveita o padrão de
  overlay que `#sidebar`/`#sidebar-toggle` já usam hoje, generalizado pras
  novas colunas.

## Fora de escopo (registrado para rodada futura)

- Card de "agente principal" no painel de contexto.
- Arquivos por conversa (upload, listagem).
- "Conversas relacionadas".
- Execução de código / análise de dados como ferramentas reais.
- Fixar/arquivar conversa (abas Todas/Fixadas/Arquivadas).
- Botões de like/dislike/regenerar em mensagens (copiar também fica de fora
  desta rodada, por decisão do usuário — nenhum botão de ação novo agora).

## Testes

- Migração de `icon` em `groups`: cobrir com teste equivalente aos de
  `tests/test_queue_worker.py`/existentes para colunas aditivas (roda em
  banco sem a coluna, confirma que `init_db()` adiciona e preenche default
  sem duplicar/perder linha).
- `GroupOut` inclui `icon` nos testes de API de grupos.
- Sem testes de "captura de tela" — é UI; verificação visual manual via
  browser preview faz parte da implementação, não da spec.
