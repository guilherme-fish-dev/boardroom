# Assistente de persona (gerar system prompt com IA)

Data: 2026-09-18
Status: Aprovado para planejamento

## Contexto e motivação

Hoje, criar um agente exige escrever o campo "Persona / system prompt" inteiro à mão. O usuário quer escrever só um esboço curto (ex.: "investidor cauteloso") e ter um modelo de IA expandir isso num system prompt completo e bem formado, reduzindo a fricção de criar novos agentes.

## Objetivo

- Um botão "Gerar com IA" no formulário de criação de agente que expande o texto atual do campo de persona usando um modelo configurável.
- Um novo campo de configuração ("modelo assistente") pra escolher qual modelo do llama-swap faz essa expansão, independente do modelo que cada agente usa pra conversar.

Fora de escopo: editar o prompt mestre (instrução fixa que orienta a expansão) pela UI — fica fixo no código; geração de persona sem nenhum esboço prévio; uso desse assistente na edição de agente existente (não há UI de edição hoje, é um gap conhecido documentado separadamente).

## Configuração

Nova chave em `settings`: `assistant_model` (string, default `""`, mesmo padrão de `default_vision_model`). Editável em **Configurações**, num campo novo "Modelo assistente (gerar personas)".

## Backend — `POST /api/agents/generate-persona`

Request: `{"draft": "<texto atual do campo de persona>", "agent_name": "<nome do agente, pode ser vazio>"}`.

Comportamento:
1. Lê `assistant_model` das settings. Se estiver vazio, responde `400` com `detail` explicando que é preciso configurar um modelo assistente em Configurações antes de usar essa função.
2. Chama `chat_completion` (já existente em `app/llm_client.py`) com `model=assistant_model` e um prompt de sistema **fixo no código**:
   > "Você expande um esboço curto de persona num system prompt detalhado para um agente de IA que participa de conversas em grupo com outros agentes de IA. Mantenha o ponto de vista central do esboço, adicione traços de personalidade, forma de argumentar, e limites claros de comportamento. Responda só com o texto do system prompt final, sem comentários extras."
   
   A mensagem do usuário nessa chamada é o `draft`, prefixado com o nome do agente quando fornecido (ex.: `"Nome do agente: bob\n\nEsboço: investidor cauteloso"`).
3. Erros de `chat_completion` (conexão recusada, timeout, resposta malformada — os mesmos tipos já tratados em `chat_completion`) viram `502` com `detail`, seguindo exatamente o padrão já usado em `GET /api/models`.
4. Sucesso: `{"persona_prompt": "<texto gerado>"}`.

## Frontend

No formulário de agente (`#agent-form`), ao lado do `<textarea id="agent-persona">`:
- Botão `<button type="button" id="generate-persona-btn">Gerar com IA</button>`, desabilitado por padrão.
- Um listener no `input` do textarea de persona habilita/desabilita o botão conforme o campo tem ou não conteúdo (trim não vazio).
- Ao clicar: desabilita o botão, muda seu texto pra "Gerando...", chama `POST /api/agents/generate-persona` com `draft` = valor atual do textarea e `agent_name` = valor atual do campo nome.
  - Sucesso: substitui o valor do textarea pelo `persona_prompt` retornado, restaura o texto do botão, reabilita.
  - Erro: mostra a mensagem de erro (do `detail` da resposta) num elemento inline abaixo do botão (`<span id="generate-persona-error">`), restaura o texto e habilita o botão de novo. A mensagem de erro é limpa a cada nova tentativa.
- O botão nunca interfere no fluxo de submit normal do formulário (é `type="button"`, não dispara submit).

Em Configurações (`#settings-form`), novo campo:
```html
<label>Modelo assistente (gerar personas)
  <input id="setting-assistant-model" />
</label>
```
Segue exatamente o mesmo padrão de carregar/salvar dos campos de settings já existentes (`loadSettings`/submit do `settings-form`).

## Testes

Backend (`tests/test_agents_api.py`, adicionando aos testes já existentes):
- `POST /api/agents/generate-persona` sem `assistant_model` configurado → `400`.
- Com `assistant_model` configurado e `chat_completion` mockado retornando texto → `200` com `{"persona_prompt": "..."}` e confirma que o prompt de sistema fixo foi usado na chamada.
- Com `chat_completion` mockado levantando exceção → `502` com `detail`.

Settings (`tests/test_settings_api.py`): `assistant_model` incluído no schema de `GET`/`PUT /api/settings`, default `""`, igual aos outros campos.

Frontend: sem teste automatizado (mesmo padrão do resto do app) — verificação manual: botão desabilitado com campo vazio, habilita ao digitar, gera e substitui o texto com sucesso, mostra erro inline quando `assistant_model` não está configurado ou o llama-swap está fora do ar.
