# Autocomplete de @menção no campo de mensagem

## Objetivo

Ao digitar `@` no campo de mensagem (`#message-input`), mostrar um dropdown com
sugestões de nomes para completar a menção, evitando erros de digitação (nomes
com acento, nomes compostos) e agilizando o fluxo de @mencionar agentes.

## Escopo

- Sugestões vêm apenas dos membros da conversa atual (`state.members`), mais a
  pseudo-entrada `all` (mencionar todos).
- Não busca agentes fora da conversa (fora de escopo — ver pergunta respondida
  durante o brainstorming).
- Só cliente (`app/static/app.js` + `app/static/style.css` + pequeno ajuste em
  `app/static/index.html` se necessário para o container do dropdown).
  Nenhuma mudança de backend: `app/mentions.py` já resolve nomes com espaço
  (`resolve_mentions`), então basta inserir o nome completo do agente no texto.

## Detecção da menção em digitação

No listener de `input` do `#message-input`:

1. A partir da posição do cursor (`selectionStart`), localizar o último `@`
   antes do cursor.
2. `query` = substring entre esse `@` e o cursor.
3. Se `query` contiver quebra de linha ou outro `@`, não é uma menção ativa →
   fechar o dropdown.
4. Filtrar candidatos: `state.members` (nome) + `all`, mantendo os cujo nome
   comece com `query` (comparação case-insensitive, sem normalizar acento).
5. Se não houver candidatos, fechar o dropdown. Se houver, (re)renderizar a
   lista com o primeiro item destacado.

Isso cobre nomes compostos (ex. "Osvaldo Tibúrcio"): como `query` pode conter
espaço, digitar `@osvaldo tib` continua filtrando corretamente.

## Interação (teclado + mouse)

- `ArrowDown` / `ArrowUp`: move o destaque entre os itens da lista (com wrap).
- `Enter` ou `Tab` com o dropdown aberto: confirma o item destacado — substitui
  o trecho `@query` por `@Nome ` (nome completo + espaço à direita) e fecha o
  dropdown. **Não deve enviar a mensagem** nesse caso (o `Enter` é interceptado
  antes do handler de envio).
- `Escape`: fecha o dropdown sem alterar o texto do input.
- Clique num item da lista: mesma confirmação do `Enter`.
- Perder o foco do input, ou o cursor sair da região de menção ativa (detectado
  no próximo evento de `input`/`keyup` de seta), fecha o dropdown.

## Renderização

Lista (`<ul id="mention-suggestions">`) posicionada em `position: absolute`,
ancorada acima do composer (que fica fixo na parte inferior da tela),
reaproveitando a paleta de cores existente. Cada item mostra o nome do agente;
o item `all` mostra um rótulo diferenciado (ex. "all — mencionar todos os
agentes").

## Fora de escopo

- Sugerir agentes que não são membros da conversa.
- Reabrir o dropdown para uma menção já "fechada" (cursor já passou por um
  espaço seguido de texto que não combina mais com nenhum nome).
- Suporte a mobile/touch além do clique básico (o app já roda em desktop).
