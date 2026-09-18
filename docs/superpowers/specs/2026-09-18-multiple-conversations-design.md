# Múltiplas conversas por grupo + apagar conversas

## Contexto

Hoje um grupo tem exatamente uma "conversa" implícita: as mensagens são ligadas
direto a `messages.group_id`. Não existe entidade `conversation`. O objetivo
desta feature é permitir que um grupo tenha várias conversas (threads
independentes de mensagens) e que o usuário possa criar, renomear e apagar
conversas.

## Modelo de dados

Nova tabela:

```sql
CREATE TABLE conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- `messages.group_id` é substituído por `messages.conversation_id`
  (`REFERENCES conversations(id) ON DELETE CASCADE`).
- `queue_jobs.group_id` é substituído por `queue_jobs.conversation_id`
  (mesma FK/cascade).
- `groups` e `group_members` não mudam.

Apagar uma conversa é **hard delete** (`DELETE FROM conversations WHERE id = ?`),
seguindo o mesmo padrão já usado em `groups` e `agents`. O `ON DELETE CASCADE`
remove as mensagens e queue_jobs associados.

## Migração de dados existentes

Em `app/db.py`, ao lado de `_ensure_hidden_kind_column`, adicionar
`_ensure_conversations_table`, executada na inicialização do banco:

1. Se a tabela `conversations` não existir:
   - Criar a tabela `conversations`.
   - Para cada grupo existente em `groups`, criar uma conversa chamada
     `"Geral"` associada a ele.
   - Adicionar a coluna `conversation_id` em `messages` e `queue_jobs`.
   - Preencher `conversation_id` de cada mensagem/queue_job com a conversa
     "Geral" do grupo correspondente (via `group_id` antigo).
   - Remover a coluna `group_id` de `messages` e `queue_jobs` (usar
     `ALTER TABLE ... DROP COLUMN` quando suportado pelo SQLite instalado;
     caso contrário, recriar a tabela via `CREATE TABLE new AS SELECT ...` +
     `DROP`/`RENAME`, seguindo o padrão de migração manual já usado no
     projeto).

Nenhuma mensagem existente é perdida: todas passam a pertencer à conversa
"Geral" do grupo em que estavam.

## API

Novo router `app/routers/conversations.py`, montado em
`/api/groups/{group_id}/conversations`:

- `GET ""` — lista conversas do grupo (id, name, created_at).
- `POST ""` — cria conversa. Body: `{"name": str}`. Nome obrigatório
  (mesma validação de nome vazio já usada em `groups`/`agents`).
- `PUT "/{conversation_id}"` — renomeia a conversa.
- `DELETE "/{conversation_id}"` — apaga a conversa.
  - Se, após a exclusão, o grupo não tiver mais nenhuma conversa, o backend
    cria automaticamente uma nova conversa "Geral" vazia para o grupo antes
    de responder — um grupo nunca fica sem conversa ativa.

`app/routers/messages.py` muda de prefixo:
`/api/conversations/{conversation_id}/messages` (era
`/api/groups/{group_id}/messages`). Os handlers passam a filtrar/inserir por
`conversation_id`.

`app/queue_worker.py` e `app/mentions.py` passam a operar sobre
`conversation_id` em vez de `group_id` (o `group_id` só é necessário para
resolver membros do grupo — a conversa aponta para o grupo, então isso é uma
junção extra: `conversations.group_id`).

Apagar um grupo continua fazendo cascade normalmente: `groups` →
`conversations` → `messages`/`queue_jobs`, todos `ON DELETE CASCADE`.

## Frontend

Em `index.html`, acima da área de mensagens (`#view-channel`), uma nova faixa
de abas de conversas (`#conversation-tabs`), visível apenas quando um grupo
está selecionado.

Em `app.js`:

- `selectGroup(groupId)` passa a carregar as conversas do grupo
  (`GET /api/groups/{id}/conversations`) e renderizar as abas.
- Uma nova `state.currentConversationId` guarda a conversa ativa. Ao trocar
  de grupo, seleciona a primeira conversa da lista.
- `selectConversation(conversationId)` troca a conversa ativa, zera
  `state.lastMessageId`, limpa `#message-list` e reinicia o polling
  (`pollMessages`) usando a nova rota de mensagens por conversa.
- Cada aba tem um "x" para apagar (com `confirm()`, mesmo padrão usado hoje
  para apagar grupo/agente). Um botão "+" abre um prompt simples para nome
  da nova conversa (mesmo padrão de rename de grupo, que já usa prompt).
- Se a conversa apagada era a ativa, o frontend seleciona a conversa
  retornada pelo backend (a nova "Geral" recriada, se for o caso, ou a
  próxima da lista).

## Casos de borda

- Apagar a última conversa de um grupo: backend recria automaticamente uma
  "Geral" vazia (ver seção API). O usuário nunca vê um grupo sem conversa.
- Apagar um grupo: cascade apaga suas conversas e mensagens normalmente, sem
  mudança de comportamento visível.
- Nome de conversa vazio: rejeitado com erro 400, mesma validação usada em
  `groups`/`agents`.

## Testes

Não há suíte de testes automatizados no projeto. Verificação manual via
browser:

1. Selecionar um grupo existente e confirmar que a conversa "Geral" aparece
   com o histórico de mensagens migrado.
2. Criar uma nova conversa, trocar entre abas e confirmar que as mensagens
   ficam isoladas por conversa.
3. Renomear uma conversa.
4. Apagar uma conversa não-última e confirmar que a aba desaparece e a
   conversa ativa muda corretamente.
5. Apagar a última conversa restante de um grupo e confirmar que uma nova
   "Geral" vazia é criada automaticamente.
6. Apagar um grupo com múltiplas conversas e confirmar que tudo é removido
   em cascata sem erros.
