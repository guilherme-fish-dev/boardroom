# Settings Model Dropdowns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two free-text model fields in Settings ("Modelo de visão padrão", "Modelo assistente") with dropdowns populated from `GET /api/models`, without changing the agent form's existing model dropdown behavior.

**Architecture:** Extract a shared `populateModelSelect()` helper from the existing `loadModels()` function in `app/static/app.js`. The agent form keeps its current required-selection, disable-submit-on-error behavior via the helper's callback hook. The two new Settings selects use the same helper with different options: an empty value is valid, a fetch failure only disables those two selects (never the Save button), and a previously-saved value no longer present in the model list is kept as a selected extra option instead of silently disappearing.

**Tech Stack:** Plain JS (no framework), no backend changes — `GET /api/models` already exists.

**Spec:** `docs/superpowers/specs/2026-09-18-settings-model-dropdowns-design.md`

---

## File Structure

```
boardroom/
  app/
    static/
      index.html    # setting-vision-model and setting-assistant-model become <select>
      app.js          # extract populateModelSelect(), refactor loadModels(), update loadSettings()
```

No backend files change. No automated tests apply (this project has none for the frontend) — every step's verification is manual, run in a real browser against the app.

---

### Task 1: Extract `populateModelSelect()` and refactor `loadModels()`

**Files:**
- Modify: `app/static/app.js`

- [ ] **Step 1: Replace `loadModels()` with the extracted helper plus a thin wrapper**

In `app/static/app.js`, find the entire existing `loadModels()` function:

```javascript
async function loadModels() {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  try {
    const data = await api("/api/models");
    modelSelect.innerHTML = "";
    modelSelect.disabled = false;
    submitButton.disabled = false;

    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "selecione um modelo";
    modelSelect.appendChild(emptyOption);

    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.appendChild(option);
    }
  } catch (err) {
    console.error("Failed to load models:", err);
    modelSelect.innerHTML = "";
    const errorOption = document.createElement("option");
    errorOption.value = "";
    errorOption.textContent = "Erro ao carregar modelos (verifique o llama-swap)";
    errorOption.disabled = true;
    errorOption.selected = true;
    modelSelect.appendChild(errorOption);
    modelSelect.disabled = true;
    submitButton.disabled = true;
  }
}
```

Replace it with:

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

async function loadModels() {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, "", {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => {
      submitButton.disabled = true;
    },
  });
  if (ok) submitButton.disabled = false;
}
```

- [ ] **Step 2: Run the full automated suite**

Run: `pytest -v`
Expected: 55 passed (this is a frontend-only change; confirms nothing in the backend broke)

- [ ] **Step 3: Manual verification — agent form dropdown unchanged**

Run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000` (with a real or mock llama-swap reachable at the configured `llama_swap_base_url`, or without one to test the error path).

Open `http://localhost:8000`, go to **Agentes**.

Expected (llama-swap reachable): `#agent-model` shows "selecione um modelo" plus the fetched models; "Salvar agente" is disabled until a model is chosen (same as before this refactor).

Expected (llama-swap unreachable): `#agent-model` shows the disabled "Erro ao carregar modelos (verifique o llama-swap)" option, and "Salvar agente" is disabled — identical to the pre-refactor behavior.

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "refactor: extract populateModelSelect() from loadModels()"
```

---

### Task 2: Convert the two Settings model fields to dropdowns

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`

- [ ] **Step 1: Change the two inputs to empty selects in `app/static/index.html`**

Find:

```html
          <label>Modelo de visão padrão
            <input id="setting-vision-model" />
          </label>
```

Replace it with:

```html
          <label>Modelo de visão padrão
            <select id="setting-vision-model"></select>
          </label>
```

Find:

```html
          <label>Modelo assistente (gerar personas)
            <input id="setting-assistant-model" placeholder="ex.: qwen2.5-7b" />
          </label>
```

Replace it with:

```html
          <label>Modelo assistente (gerar personas)
            <select id="setting-assistant-model"></select>
          </label>
```

- [ ] **Step 2: Wire both selects in `loadSettings()`**

In `app/static/app.js`, find:

```javascript
async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-vision-model").value = settings.default_vision_model;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;
  document.getElementById("setting-assistant-model").value = settings.assistant_model;
}
```

Replace it with:

```javascript
async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;

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
}
```

(Note: the two `populateModelSelect` calls each make their own `GET /api/models` request. This is intentionally simple — no caching — matching this codebase's existing style of not caching `GET /api/models` between calls, e.g. `loadModels()` already re-fetches every time the Agents view opens.)

- [ ] **Step 3: Run the full automated suite**

Run: `pytest -v`
Expected: 55 passed (still a frontend-only change)

- [ ] **Step 4: Manual verification — success path and persistence**

With a reachable llama-swap, run the server, open **Configurações**.

Expected: both "Modelo de visão padrão" and "Modelo assistente" show as dropdowns populated with the models from llama-swap, each showing "nenhum (não configurado)" plus the fetched list. If a value was previously saved, it's pre-selected.

Pick a model in "Modelo assistente", click "Salvar", reload the page, go back to Configurações.

Expected: the saved model is still selected in "Modelo assistente" after reload.

- [ ] **Step 5: Manual verification — orphan value preserved**

With the server running, set an arbitrary value not present in your llama-swap's model list directly via the API:

Run: `curl -s -X PUT http://localhost:8000/api/settings -H "Content-Type: application/json" -d "{\"llama_swap_base_url\": \"http://localhost:8080\", \"default_vision_model\": \"\", \"max_pending_per_group\": \"20\", \"assistant_model\": \"modelo-que-nao-existe\"}"`

Reload the app, open Configurações.

Expected: "Modelo assistente" shows `modelo-que-nao-existe (não encontrado no llama-swap)` as the selected option, in addition to the real models from llama-swap. It is not silently reset to empty.

Reset it back afterward by selecting "nenhum (não configurado)" (or a real model) and saving, to leave Settings in a clean state.

- [ ] **Step 6: Manual verification — llama-swap unreachable doesn't block Save**

Stop llama-swap (or point `llama_swap_base_url` at an unreachable address via a direct `PUT /api/settings` call, same pattern as Step 5, e.g. `http://localhost:9`), then reload the app and open Configurações.

Expected: both model selects show the disabled "Erro ao carregar modelos (verifique o llama-swap)" option. The "URL base do llama-swap" text field and the "Salvar" button remain fully usable — change the URL field back to the correct `llama_swap_base_url` and click Salvar.

Expected: the save succeeds (the request goes through even though the two model selects were disabled — their disabled `<select>` elements still submit their current `value`, which is the previously-saved model name preserved from Step 1/5, not lost).

Reload the page after fixing the URL to confirm the model dropdowns populate correctly again.

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: convert Settings model fields to dropdowns populated from llama-swap"
```

---

## Self-Review Notes

- **Spec coverage:** shared helper with `allowEmpty`/`emptyLabel`/`onError` (Task 1), both Settings selects using it without `onError` so Save is never blocked (Task 2), orphan-value preservation (Task 2, Step 5), agent form behavior unchanged (Task 1, Step 3) — all requirements from `docs/superpowers/specs/2026-09-18-settings-model-dropdowns-design.md` are covered.
- **Disabled `<select>` submits its value:** confirmed as a real HTML behavior worth calling out explicitly in Task 2 Step 6 — a `disabled` `<select>` element does NOT get included in a native form submission, but this app doesn't use native form submission (it reads `.value` directly via `document.getElementById(...).value` in the `settings-form` submit handler, which already exists and is unchanged by this plan), so the disabled select's last-set `.value` is still read and sent normally. No code change needed for this to work correctly — just confirmed by the verification step.
- **No backend changes**, confirmed against the spec's explicit scope — `GET /api/models` is reused as-is.
