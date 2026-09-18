# Web search para os agentes

Data: 2026-09-18
Status: Aprovado para planejamento

## Contexto e motivação

Os agentes hoje só sabem o que está no próprio modelo (sem acesso a informação atual) e o que está no histórico da conversa. O usuário quer dar a eles a capacidade de pesquisar na internet quando precisarem de informação que não têm — inspirado em como o `opencode` (agente de código CLI) implementa isso: delegando a busca a um serviço remoto (Exa), exposto como uma "tool" que o modelo aciona.

Investigação do binário do `opencode` (`C:\Users\guilh\AppData\Roaming\npm\node_modules\opencode-ai\...\opencode.exe`) confirmou que ele chama `https://mcp.exa.ai/mcp` via protocolo JSON-RPC 2.0 (`method: "tools/call"`, tool remota `web_search_exa`), funcionando sem API key (usa o tier público/gratuito da Exa; só autentica se o usuário tiver `EXA_API_KEY` configurada, o que não é o nosso caso).

## Objetivo

- Todo agente ganha, automaticamente, a capacidade de pesquisar na web durante seu turno de resposta (sem flag por agente — decisão do usuário: "todos os agentes sempre podem buscar").
- Usa o mesmo endpoint público que o `opencode` usa (Exa MCP), sem exigir cadastro/API key.
- Mecanismo de acionamento: padrão de texto simples (`BUSCAR: <consulta>`), não tool-calling nativo — mais compatível com os modelos pequenos que costumam rodar em llama.cpp local.
- Limite de 3 buscas por turno de resposta, pra não estourar latência/custo de um único job da fila.
- Resultado de busca vira uma mensagem oculta persistente no histórico do grupo (mesmo padrão de descrição de imagem), visível pra outros agentes em turnos futuros.

Fora de escopo: UI que mostre visualmente que um agente está buscando (o "queue-indicator" continua genérico); configuração de provedor alternativo; tool-calling nativo (formato OpenAI `tools`); cache de resultados de busca entre consultas repetidas.

## Provedor: Exa MCP público

Novo módulo `app/web_search.py`:

```python
from __future__ import annotations

import json

import httpx

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def _parse_mcp_response(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError(f"resposta SSE sem linha 'data:': {response.text}")
    return response.json()


def web_search(
    query: str,
    *,
    num_results: int = 5,
    http_client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> str:
    """Search the web via Exa's public MCP endpoint (no API key required).

    Raises:
        httpx.ConnectError: if the endpoint is unreachable.
        httpx.TimeoutException: if the request times out.
        httpx.HTTPStatusError: if the endpoint responds with an error status.
        ValueError: if the response body doesn't have the expected shape.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {"query": query, "numResults": num_results},
        },
    }

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.post(
            EXA_MCP_URL,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = _parse_mcp_response(response)
        try:
            return data["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"resposta inesperada da busca: {data}") from exc
    finally:
        if owns_client:
            client.close()
```

Segue exatamente o mesmo padrão de erro/ciclo de vida de client já estabelecido em `app/llm_client.py` (`chat_completion`, `list_models`): erros de rede propagam, resposta malformada vira `ValueError`, client próprio fecha, client injetado (usado em teste) não.

O endpoint da Exa pode responder em JSON puro ou SSE (`text/event-stream`) dependendo do header `Accept` — `_parse_mcp_response` trata os dois formatos, replicando o que o `opencode` faz internamente.

## Migração de schema: `hidden_kind`

Hoje, mensagens ocultas (`hidden=1`) só existem pra descrição de imagem. Com resultado de busca também virando mensagem oculta, é preciso diferenciar os dois tipos — porque o código de `_build_history` já tem uma lógica que EXCLUI mensagens ocultas do histórico quando um agente com visão vai receber a imagem original direto (pra não duplicar a descrição textual + a imagem). Sem diferenciar, essa exclusão apagaria também resultados de busca por engano.

Nova coluna `hidden_kind TEXT` na tabela `messages` (valores: `'image_description'`, `'search_result'`, ou `NULL` pra mensagens normais). Em `app/db.py`:

- `SCHEMA` (definição de `CREATE TABLE messages`) ganha a coluna `hidden_kind TEXT` pra bancos novos.
- `init_db()` ganha uma migração idempotente pra bancos já existentes (a primeira migração de schema real do projeto):

```python
def _ensure_hidden_kind_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "hidden_kind" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN hidden_kind TEXT")
```

Chamada dentro de `init_db()`, depois de `conn.executescript(SCHEMA)` e antes do commit.

`_process_describe_image` (em `app/queue_worker.py`) passa a gravar `hidden_kind='image_description'` no INSERT existente.

`_build_history` troca o parâmetro `exclude_hidden: bool` por `exclude_image_descriptions: bool`, e a cláusula SQL correspondente muda de `AND hidden = 0` pra `AND NOT (hidden = 1 AND hidden_kind = 'image_description')` — ou seja, só filtra a descrição de imagem redundante, nunca resultados de busca.

## Mecanismo de busca: padrão de texto + loop no worker

Toda persona de agente recebe uma instrução fixa extra, acrescentada dentro de `_build_history` ao montar a mensagem de sistema:

```python
WEB_SEARCH_INSTRUCTIONS = (
    "\n\nVocê pode pesquisar na internet quando precisar de informação atual ou que não sabe. "
    "Para isso, responda usando SOMENTE esta linha, nada mais: BUSCAR: sua consulta aqui. "
    "Você vai receber os resultados da busca e poderá responder normalmente em seguida, "
    "ou buscar de novo (no máximo 3 vezes) se ainda precisar de mais informação."
)
```

Em `app/queue_worker.py`, `_process_agent_turn` passa a fazer um loop depois da primeira chamada ao modelo:

```python
SEARCH_PATTERN = re.compile(r"^\s*BUSCAR:\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
MAX_SEARCHES_PER_TURN = 3
```

1. Chama `chat_completion` normalmente (com a imagem, se aplicável) e recebe `reply`.
2. Se `reply` (inteira, sem mais nada em volta) bater com `SEARCH_PATTERN`, extrai a consulta, chama `web_search(query)`. Se a busca falhar (rede, resposta malformada), o texto do erro vira o "resultado" devolvido ao modelo — a falha de busca não derruba o job, só vira contexto pro modelo decidir como continuar.
3. Grava o resultado como mensagem oculta (`hidden=1, hidden_kind='search_result'`) no grupo.
4. Acrescenta ao histórico em memória (não persistido além da mensagem oculta) a resposta do agente (`assistant`) + o resultado da busca (`user`), e chama o modelo de novo.
5. Repete até 3 vezes. Se, mesmo depois da 3ª busca, o modelo ainda responder com `BUSCAR:`, uma última chamada força o encerramento: acrescenta uma mensagem dizendo que o limite foi atingido e pede uma resposta final com o que já se sabe.
6. Só a resposta final (que não bate com `SEARCH_PATTERN`) vira a mensagem de verdade do agente no chat (mesmo INSERT que já existe hoje) — as respostas intermediárias com `BUSCAR:` nunca aparecem como mensagem visível nem geram menção.

Isso mantém a garantia central do projeto — processar um agente por vez, sequencialmente — porque tudo isso acontece dentro de uma única execução de `_process_agent_turn`, sem paralelismo novo: só significa que um job individual pode fazer até 5 chamadas ao modelo (1 inicial + até 3 de busca + 1 forçada) em vez de 1, sequencialmente, antes de terminar.

## Testes

Backend:
- `tests/test_web_search.py` (novo): `web_search()` com `httpx.MockTransport` — resposta JSON de sucesso, resposta SSE de sucesso, erro HTTP, resposta malformada (`ValueError`).
- `tests/test_db.py`: migração `hidden_kind` idempotente (rodar `init_db()` duas vezes não quebra; coluna existe depois).
- `tests/test_queue_worker.py`: 
  - agente busca uma vez, recebe resultado, responde normalmente (mock de `chat_completion` retornando `"BUSCAR: clima em SP"` na primeira chamada e uma resposta normal na segunda; mock de `web_search` retornando um texto fixo) — confirma que a mensagem final salva é a segunda resposta, não a primeira, e que existe uma mensagem oculta com `hidden_kind='search_result'`.
  - agente atinge o limite de 3 buscas e é forçado a responder.
  - falha na busca (mock de `web_search` levantando exceção) não derruba o job — o erro vira texto de contexto e o job termina `done` normalmente.
  - agente com visão recebendo imagem direta continua excluindo só a descrição de imagem do histórico, não resultados de busca anteriores (teste de regressão pro comportamento já existente, agora com a lógica renomeada).

Sem mudança de frontend nessa primeira etapa (o resultado de busca aparece pro usuário só indiretamente, dentro da resposta final do agente — não há UI nova).
