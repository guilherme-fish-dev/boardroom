# Dropdowns de modelo em Configurações

Data: 2026-09-18
Status: Aprovado para planejamento

## Contexto e motivação

O formulário de criação de agente já tem um dropdown de modelo populado a partir de `GET /api/models` (llama-swap). Os dois campos de modelo em **Configurações** ("Modelo de visão padrão" e "Modelo assistente (gerar personas)") ainda são texto livre — o usuário precisa saber o nome exato do modelo de cor, o que já causou confusão (ex.: o campo mostrando literalmente `"undefined"` quando o valor não vinha preenchido).

## Objetivo

Trocar os dois campos de modelo em Configurações por `<select>` populados da mesma forma que o campo de modelo do agente, mas com semântica diferente: aqui, "nenhum modelo selecionado" é um estado válido (significa "não configurado"), e uma falha ao buscar os modelos do llama-swap **não** deve impedir o resto do formulário de ser salvo.

Fora de escopo: mudança de backend (nenhuma — `GET /api/models` já existe e é reaproveitado como está); autocomplete/busca dentro do dropdown; qualquer mudança no dropdown do formulário de agente além da refatoração interna necessária pra compartilhar código.

## Diferenças de comportamento vs. o dropdown do formulário de agente

| | Formulário de agente (`#agent-model`) | Configurações (`#setting-vision-model`, `#setting-assistant-model`) |
|---|---|---|
| Opção vazia | Não permitida como valor final — precisa escolher algo antes de salvar | Permitida e é um estado válido ("não configurado") |
| Falha ao buscar `/api/models` | Desabilita o select **e** o botão de submit inteiro | Desabilita só aquele select específico; o botão "Salvar" do formulário continua habilitado (você pode estar corrigindo a própria URL do llama-swap nesse mesmo formulário) |
| Valor salvo que não está mais na lista do llama-swap | N/A (agente novo não tem valor prévio) | Mantido como uma opção extra, selecionada, mesmo fora da lista atual — não perde o que já estava configurado |

## Frontend

**Refatoração** — extrair de dentro do `loadModels()` atual (em `app/static/app.js`) uma função genérica:

```javascript
async function populateModelSelect(selectEl, currentValue, { allowEmpty, emptyLabel, onError } = {}) {
  try {
    const data = await api("/api/models");
    selectEl.innerHTML = "";
    selectEl.disabled = false;

    if (allowEmpty) {
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = emptyLabel;
      selectEl.appendChild(emptyOption);
    }

    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      selectEl.appendChild(option);
    }

    if (currentValue && !data.models.includes(currentValue)) {
      const orphanOption = document.createElement("option");
      orphanOption.value = currentValue;
      orphanOption.textContent = `${currentValue} (não encontrado no llama-swap)`;
      selectEl.appendChild(orphanOption);
    }

    selectEl.value = currentValue || "";
    return true;
  } catch (err) {
    console.error("Failed to load models:", err);
    selectEl.innerHTML = "";
    const errorOption = document.createElement("option");
    errorOption.value = currentValue || "";
    errorOption.textContent = "Erro ao carregar modelos (verifique o llama-swap)";
    errorOption.disabled = true;
    errorOption.selected = true;
    selectEl.appendChild(errorOption);
    selectEl.disabled = true;
    if (onError) onError(err);
    return false;
  }
}
```

`loadModels()` (usado pelo formulário de agente) passa a ser:

```javascript
async function loadModels() {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, "", {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => { submitButton.disabled = true; },
  });
  if (ok) submitButton.disabled = false;
}
```

(Mantém exatamente o comportamento atual: começa vazio, exige seleção, desabilita o botão de submit em caso de erro.)

`loadSettings()` passa a, depois de setar os valores dos campos de texto, chamar:

```javascript
await populateModelSelect(
  document.getElementById("setting-vision-model"),
  settings.default_vision_model,
  { allowEmpty: true, emptyLabel: "nenhum (não configurado)" }
);
await populateModelSelect(
  document.getElementById("setting-assistant-model"),
  settings.assistant_model,
  { allowEmpty: true, emptyLabel: "nenhum (não configurado)" }
);
```

Sem `onError`, então uma falha ao buscar `/api/models` só desabilita aqueles dois selects — o botão "Salvar" do `settings-form` nunca é tocado por essa função, continuando disponível pra salvar os outros campos (URL base, limite de jobs) normalmente.

Em `app/static/index.html`, `#setting-vision-model` e `#setting-assistant-model` viram `<select>` vazios (populados via JS, igual `#agent-model`).

## Testes

Sem mudança de backend, então sem novos testes de API. Verificação manual (mesmo padrão já usado nas telas de frontend deste projeto):
1. Com llama-swap respondendo: abrir Configurações, confirmar que os dois selects vêm populados e com o valor salvo pré-selecionado.
2. Salvar um valor num dos selects, recarregar a página, confirmar que persiste.
3. Configurar um agente/settings com um nome de modelo que não existe na lista atual do llama-swap (editar direto no banco ou via `PUT /api/settings` com um valor arbitrário), abrir Configurações, confirmar que aparece como opção extra selecionada, com o sufixo "(não encontrado no llama-swap)".
4. Com llama-swap fora do ar: abrir Configurações, confirmar que os dois selects ficam desabilitados com a mensagem de erro, mas o campo de URL e o botão "Salvar" continuam funcionando — mudar a URL base e salvar com sucesso.
5. Confirmar que o dropdown do formulário de agente (`#agent-model`) mantém exatamente o comportamento anterior (obrigatório, desabilita "Salvar agente" em caso de erro) depois da refatoração.
