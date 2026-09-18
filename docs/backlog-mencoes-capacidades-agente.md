# Melhorias pendentes — menções e capacidades do agente

Data: 2026-09-18
Status: Backlog (não implementado ainda)

## 1. Preservar ordem das menções

**Problema:** hoje, quando uma mensagem menciona vários agentes de uma vez
(ex.: `@bob @alice @carla`), `enqueue_mentions` (`app/routers/messages.py`)
cria um job por agente encontrado, mas a query SQL que resolve os nomes
não tem `ORDER BY` — a ordem de resposta não é garantida bater com a ordem
em que os nomes foram escritos no texto.

**Comportamento esperado:** os agentes devem responder na mesma ordem em
que foram mencionados na mensagem (da esquerda pra direita).

**Onde mexer:** `extract_mentions` (`app/mentions.py`) já preserva ordem
de aparição no texto (dedup mantendo a primeira ocorrência). O problema
está em `enqueue_mentions`, que depois busca os IDs via `SELECT ... WHERE
lower(agents.name) IN (...)` sem preservar a ordem da lista `names`. Precisa
either (a) iterar sobre `names` na ordem e fazer um SELECT por nome (mais
simples, N queries pequenas), ou (b) usar `ORDER BY CASE agents.name WHEN
... END` pra manter a ordem numa query só.

Também vale considerar: como os jobs de agent_turn hoje só diferenciam
prioridade 1 (todos) + FIFO por `id`, garantir que os INSERTs em
`queue_jobs` aconteçam na ordem certa dentro de `enqueue_mentions` já
resolve — o worker processa por `id ASC` dentro da mesma prioridade.

## 2. Deixar explícito pro modelo quais capacidades ele tem

**Problema:** hoje o agente só sabe usar `BUSCAR: <consulta>` (busca web)
porque isso está fixo em `WEB_SEARCH_INSTRUCTIONS`
(`app/queue_worker.py`), mas não existe nenhuma instrução dizendo
explicitamente ao modelo que ele pode **mencionar outros agentes do grupo**
usando `@nome-exato` pra trazer alguém pra conversa ou encadear uma
sequência de respostas (ex.: investidor conservador → agressivo →
validador de fatos).

Sem essa instrução, encadear menções depende de comportamento emergente
do modelo (ver conversa anterior) — não é confiável.

**Comportamento esperado:** o system prompt de cada agente deve deixar
claro, de forma parecida com `WEB_SEARCH_INSTRUCTIONS`:
- Que capacidades ele tem disponíveis agora (hoje: só busca na web).
- Que ele pode mencionar (`@nome`) outros agentes que fazem parte do
  mesmo grupo, pra trazer a opinião deles ou encadear uma sequência de
  respostas — e que precisa usar o nome exato cadastrado do agente.

**Onde mexer:** provavelmente em `_build_history`
(`app/queue_worker.py`), no mesmo lugar onde `WEB_SEARCH_INSTRUCTIONS` é
concatenada à persona — acrescentar uma instrução sobre menções, e
possivelmente passar a lista de nomes dos outros membros do grupo (pra o
modelo saber quem existe e pode ser mencionado, já que hoje ele não tem
essa lista explícita em lugar nenhum do prompt).

## Observação

Essas duas melhorias se relacionam: a primeira garante ORDEM correta
quando você menciona vários de uma vez; a segunda ajuda o modelo a
**decidir mencionar** sozinho (sem você precisar mencionar todo mundo
manualmente). Fazem sentido como duas tasks separadas, mas cabe brainstorm
conjunto já que mexem no mesmo trecho (`_build_history`/`enqueue_mentions`).
