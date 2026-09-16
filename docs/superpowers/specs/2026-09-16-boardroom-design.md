# Boardroom — Chat local com times de agentes IA

Data: 2026-09-16
Status: Aprovado para planejamento

## Contexto e motivação

Tentativa anterior ("Buzz") era um clone de Slack onde agentes de IA conversavam entre si, mas rodava as respostas em paralelo. Com VRAM limitada (modelo local via llama.cpp), a concorrência causava problemas. Boardroom resolve isso processando **uma resposta de agente por vez**, numa fila sequencial, mantendo a experiência de "vários participantes conversando" sem estourar recursos.

Caso de uso principal: criar grupos temáticos de agentes com personas diferentes (ex.: "Investidores" com um conservador, um agressivo, um focado em carreira) e discutir uma decisão com eles, participando ativamente da conversa.

## Objetivo do v1

Site local funcional para:
- Criar agentes (nome + persona/prompt + modelo).
- Agrupar agentes em canais/grupos.
- Conversar em um canal como mais um participante (você + agentes).
- Usar `@menção` para direcionar quem deve responder.
- Anexar imagens e ter os agentes discutindo sobre elas.

Fora de escopo do v1 (explicitamente adiado): avatares/pixel art, moderação automática de consenso, execução paralela de agentes, múltiplas instâncias de modelo.

## Stack

- **Backend:** Python + FastAPI.
- **Banco:** SQLite (arquivo local, sem servidor externo).
- **Frontend:** HTML/JS simples servido pelo próprio FastAPI (sem build step, sem framework pesado) — prioridade é ter algo funcional rápido.
- **Modelos:** llama.cpp via **llama-swap** como proxy (API compatível OpenAI em `/v1/chat/completions`), rodando localmente. Boardroom não gerencia processo de modelo — só faz requests especificando o `model` desejado por chamada, e o llama-swap troca o modelo carregado sob demanda.

## Modelo de dados (SQLite)

**agents**
- `id`, `name`, `persona_prompt` (system prompt), `model_name` (alias no llama-swap), `vision_capable` (bool), `created_at`

**groups** (canais)
- `id`, `name`, `created_at`

**group_members**
- `group_id`, `agent_id`

**messages**
- `id`, `group_id`, `sender_type` (`user` | `agent` | `system`), `sender_id` (null se `user`/`system`), `content`, `image_path` (nullable), `hidden` (bool, default false), `created_at`

**queue_jobs**
- `id`, `group_id`, `agent_id` (null para jobs de descrição de imagem), `job_type` (`agent_turn` | `describe_image`), `priority` (0 = alta / descrição de imagem, 1 = normal / turno de agente), `payload` (ex.: `image_path` para describe_image, `trigger_message_id` para agent_turn), `status` (`pending` | `processing` | `done` | `error`), `created_at`

## Configuração global

Um único arquivo/tabela de settings com:
- `llama_swap_base_url` (default `http://localhost:8080`)
- `default_vision_model` (alias no llama-swap usado para gerar descrições ocultas de imagem)

## Mecânica de fila e menções

1. Toda mensagem nova (de usuário ou de agente) é parseada em busca de `@nome-do-agente`.
2. Para cada agente mencionado que é membro do grupo, cria-se um `queue_job` do tipo `agent_turn` (prioridade 1), referenciando a mensagem que disparou.
3. Existe **um worker global assíncrono único** no processo do backend. Ele processa a fila (`queue_jobs` com status `pending`) em ordem de **prioridade, depois FIFO**: jobs `describe_image` (prioridade 0) sempre antes de `agent_turn` (prioridade 1).
4. Ao processar um `agent_turn`:
   - Monta o histórico do canal (mensagens visíveis + mensagens ocultas de descrição de imagem) como contexto.
   - Chama o llama-swap com `model = agent.model_name`.
   - Se `agent.vision_capable = true` e há imagem recente relevante no contexto, envia a imagem diretamente na mensagem multimodal (além ou em vez do texto de descrição).
   - Salva a resposta como nova `message` (`sender_type = agent`).
   - Re-escaneia a resposta por novas `@menções` e enfileira novos jobs.
5. Sem menção = sem resposta automática. A conversa avança só quando alguém (você ou um agente) menciona outro participante. Não há encerramento automático por "consenso" no v1 — se quiser esse comportamento, basta criar um agente com persona de moderador e mencioná-lo quando quiser fechar a discussão.
6. **Proteção contra loop:** limite configurável (default 20) de jobs `pending`/`processing` simultâneos por grupo. Ao atingir o limite, novas menções não geram novos jobs até a fila esvaziar — evita ciclos A→B→A→B consumindo a GPU indefinidamente. Mensagem de aviso no canal quando isso acontece.

## Pipeline de imagem

1. Usuário anexa uma imagem a uma mensagem no canal (upload salvo em `./data/uploads/`, referenciado por `image_path`).
2. Imediatamente é criado um `queue_job` do tipo `describe_image` (prioridade 0), contendo o `image_path`.
3. O worker, ao processar esse job, chama o llama-swap com `model = settings.default_vision_model`, pedindo uma descrição textual detalhada e objetiva da imagem.
4. A descrição vira uma `message` com `hidden = true` no mesmo grupo (não aparece na UI do chat, mas entra no contexto de qualquer agente que responder depois).
5. Só depois disso a fila normal de `agent_turn` (que pode já ter sido criada pelas menções na mesma mensagem) é processada — a prioridade garante que a descrição sempre existe antes de qualquer agente sem visão precisar dela.
6. Agentes com `vision_capable = true` ignoram a descrição oculta e recebem a imagem original diretamente na chamada multimodal.

## Frontend (v1)

- **Sidebar:** lista de grupos/canais + botão "novo grupo".
- **Tela de canal:** histórico de mensagens (mensagens ocultas não aparecem), indicador simples de "fila: N pendente(s) / gerando agora: <agente>", caixa de texto com suporte a `@menção` (autocomplete dos membros do grupo) e upload de imagem.
- **Tela de agentes:** listar/criar/editar agentes (nome, persona, `model_name`, `vision_capable`).
- **Tela de grupo (edição):** nome, membros (adicionar/remover agentes).
- **Tela de configurações:** `llama_swap_base_url`, `default_vision_model`, limite de jobs por grupo.

Atualização da UI: polling simples (ex.: a cada 1-2s buscar mensagens novas do canal aberto) é suficiente pro v1 — sem necessidade de WebSocket.

## Erros e casos de borda

- llama-swap indisponível/erro na chamada: job marcado `error`, mensagem de erro visível no canal como `system`, não trava o worker (segue pro próximo job da fila).
- Menção a agente que não é membro do grupo: ignorada silenciosamente (não gera job).
- Imagem sem `default_vision_model` configurado: job `describe_image` falha com mensagem de erro clara pedindo pra configurar em Settings.

## Testes (v1)

- Parsing de `@menções` (casos: múltiplas menções, menção inexistente, menção de não-membro).
- Ordenação da fila por prioridade + FIFO.
- Limite de loop por grupo.
- Construção do payload multimodal vs. texto puro conforme `vision_capable`.
- Fluxo de upload → job de descrição → mensagem oculta → contexto disponível pro próximo `agent_turn`.
