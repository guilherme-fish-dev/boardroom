# Dropdown de modelo + correção visual da flag de visão

Data: 2026-09-17
Status: Aprovado para planejamento

## Contexto e motivação

Na tela de Agentes, o campo "Nome do modelo no llama-swap" é hoje um texto livre — o usuário precisa saber de cor o alias exato configurado no llama-swap e digitar sem erro de digitação. Como o llama-swap já expõe a lista de modelos configurados via `GET /v1/models` (padrão OpenAI-compatible), dá pra transformar isso num dropdown.

Separadamente, a checkbox "Capaz de visão" aparece visualmente desalinhada do seu texto — causa raiz identificada: o CSS `form input, form textarea { width: 100%; }` também se aplica ao `<input type="checkbox">`, esticando-o e quebrando o alinhamento com o `<label>`.

## Objetivo

1. Corrigir o CSS pra que a checkbox de visão fique alinhada com seu texto.
2. Trocar o campo de modelo (na criação de agente) de texto livre pra um `<select>` populado com os modelos disponíveis no llama-swap configurado.

Fora de escopo: edição de agente existente (não há UI de edição hoje — é um gap conhecido e documentado separadamente); cache da lista de modelos entre navegações; suporte a múltiplos llama-swap/endpoints simultâneos.

## Correção CSS

Em `app/static/style.css`:
- O seletor `form input, form textarea { padding: 6px; margin-bottom: 6px; width: 100%; }` passa a excluir checkboxes: `form input:not([type="checkbox"]), form textarea { ... }`.
- O `label` que envolve a checkbox de visão (`<label><input id="agent-vision" type="checkbox" /> Capaz de visão</label>` em `app/static/index.html`) ganha uma classe (`.checkbox-label`) estilizada como `display: inline-flex; align-items: center; gap: 6px;`.

## Backend — listar modelos do llama-swap

**`app/llm_client.py`** ganha uma nova função:

```python
def list_models(*, base_url: str, http_client: httpx.Client | None = None, timeout: float = 10.0) -> list[str]
```

Chama `GET {base_url}/v1/models`, espera uma resposta no formato OpenAI-compatible (`{"data": [{"id": "...", ...}, ...]}`), retorna a lista de `id`s. Segue o mesmo padrão de erro do `chat_completion` já existente: `response.raise_for_status()` propaga `httpx.HTTPStatusError`, erros de conexão/timeout propagam como as exceções nativas do `httpx` (`ConnectError`, `TimeoutException`), e uma resposta malformada (sem `data`, ou item sem `id`) levanta `ValueError` com mensagem clara — mesmo tratamento de "resposta inesperada" já aplicado em `chat_completion`.

**Novo router `app/routers/models.py`**, `GET /api/models`:
- Lê `llama_swap_base_url` da tabela `settings`.
- Chama `list_models(base_url=...)`.
- Sucesso: `{"models": ["qwen2.5-7b", "llava-7b", ...]}`.
- Falha (qualquer exceção de `list_models`): `HTTPException(502, detail="não foi possível buscar modelos do llama-swap: <motivo>")`.

Registrado em `app/main.py` junto aos demais routers.

## Frontend — dropdown com estado de erro

Em `app/static/index.html`, o campo de modelo do formulário de agente passa de:
```html
<input id="agent-model" placeholder="Nome do modelo no llama-swap" required />
```
para:
```html
<select id="agent-model" required></select>
```

Em `app/static/app.js`:
- Nova função `async function loadModels()`: chama `GET /api/models`.
  - Sucesso: popula `#agent-model` com `<option value="">selecione um modelo</option>` seguido de uma `<option>` por modelo retornado. Reabilita o `<select>` e o botão de submit do formulário de agente, caso estivessem desabilitados de uma tentativa anterior com erro.
  - Falha: define `#agent-model` com uma única `<option value="" selected disabled>Erro ao carregar modelos (verifique o llama-swap)</option>`, desabilita o `<select>` e desabilita o botão de submit do formulário de agente (`#agent-form button[type="submit"]`), sem afetar mais nada na tela (navegação entre views continua funcionando normalmente).
- `loadModels()` é chamada toda vez que a view de Agentes é aberta (mesmo ponto onde `loadAgents()` já é chamado hoje), sem cache — uma nova tentativa de buscar os modelos acontece a cada visita à tela.
- O handler de submit do formulário de agente (`#agent-form`) não precisa de validação extra: com o botão desabilitado em caso de erro, e `required` no `<select>`, o navegador já impede submissão sem uma opção válida selecionada.

## Testes

Backend (`tests/test_models_api.py`, novo arquivo):
- `GET /api/models` com llama-swap simulado (via `httpx.MockTransport` injetado, mesmo padrão de `tests/test_llm_client.py`) retornando uma lista de modelos → resposta 200 com `{"models": [...]}` correto.
- `GET /api/models` com llama-swap retornando erro HTTP → 502 com mensagem no `detail`.
- `GET /api/models` com llama-swap inacessível (erro de conexão simulado) → 502 com mensagem no `detail`.
- Teste unitário de `list_models` em `tests/test_llm_client.py` (payload válido, payload malformado levanta `ValueError`, erro HTTP propaga).

Frontend: sem framework de teste automatizado (mesmo padrão já usado nas demais telas) — verificação manual: abrir tela de Agentes com llama-swap respondendo (dropdown populado), e com llama-swap fora do ar (dropdown desabilitado com mensagem de erro, botão de salvar desabilitado). Verificar visualmente que a checkbox de visão ficou alinhada com o texto.
