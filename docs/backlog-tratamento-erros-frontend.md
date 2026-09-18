# Backlog — chamadas `api()` sem tratamento de erro no frontend

Data: 2026-09-18
Status: Backlog (não implementado ainda)

## Problema

Vários `onsubmit`/`onclick` em `app/static/app.js` chamam o helper `api(...)`
sem `try/catch` (ex.: `agent-form` submit, `delete-agent-btn`,
`rename-group-btn`, `delete-group-btn`, `new-group-form`, `message-form`,
adicionar/remover membro). Quando a chamada falha (ex.: 404 porque outra
aba/sessão já apagou o recurso, 409 de nome duplicado, erro de rede), `api()`
lança um `Error` que não é tratado em nenhum desses handlers — a falha é
engolida silenciosamente como uma rejeição de promise não tratada, sem
feedback nenhum ao usuário.

Identificado durante a revisão final da feature de CRUD completo de agentes e
grupos (branch `feature/agent-group-crud`), ao analisar o cenário de duas
abas concorrentes editando o mesmo agente. Não é uma regressão dessa feature
— é um padrão pré-existente em todo o arquivo — mas a feature tornou mais
comum a chance de colisão (editar/apagar concorrente).

## Comportamento esperado

Pelo menos as ações destrutivas ou de escrita mais prováveis de falhar
(criar/editar/apagar agente, criar/renomear/apagar grupo, enviar mensagem)
deveriam mostrar algum feedback visível ao usuário quando a chamada falha,
em vez de falhar silenciosamente. Não precisa ser algo elaborado — um
`alert()` ou uma área de erro reaproveitável já resolveria a maior parte dos
casos.

## Onde mexer

`app/static/app.js` — provavelmente vale um helper único (ex.:
`function reportError(err) { ... }`) chamado em `.catch()` nos handlers que
hoje não tratam erro, em vez de duplicar lógica de exibição em cada um.
