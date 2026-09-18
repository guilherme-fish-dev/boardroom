const state = {
  groups: [],
  activeGroupId: null,
  conversations: [],
  activeConversationId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
  editingAgentId: null,
};

const AGENT_HUES = [200, 280, 340, 130, 20, 245, 165, 305];

function hashHue(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AGENT_HUES[hash % AGENT_HUES.length];
}

function agentColor(name) {
  return `oklch(0.72 0.13 ${hashHue(name)})`;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  if (resp.status === 204) return null;
  return resp.json();
}

function showView(name) {
  state.activeView = name;
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  }
  document.getElementById("nav-agents").classList.toggle("active", name === "agents");
  document.getElementById("nav-settings").classList.toggle("active", name === "settings");
  if (name !== "channel") {
    for (const li of document.querySelectorAll("#group-list li")) {
      li.classList.remove("active");
    }
  }
  closeSidebarOnMobile();
}

function closeSidebarOnMobile() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", "false");
}

async function loadGroups() {
  state.groups = await api("/api/groups");
  const list = document.getElementById("group-list");
  list.innerHTML = "";

  if (state.groups.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum grupo ainda";
    list.appendChild(li);
    return;
  }

  for (const group of state.groups) {
    const li = document.createElement("li");
    li.textContent = group.name;
    li.className = state.activeView === "channel" && group.id === state.activeGroupId ? "active" : "";
    li.onclick = () => selectGroup(group.id);
    list.appendChild(li);
  }
}

async function selectGroup(groupId) {
  state.activeGroupId = groupId;
  const group = state.groups.find((g) => g.id === groupId);
  document.getElementById("channel-header-name").textContent = group ? `# ${group.name}` : "";
  document.getElementById("channel-empty").classList.add("hidden");
  document.getElementById("channel-content").classList.remove("hidden");
  showView("channel");
  await loadGroups();
  await loadMembers(groupId);
  await loadConversations(groupId);
}

async function loadConversations(groupId) {
  state.conversations = await api(`/api/groups/${groupId}/conversations`);
  const stillActive = state.conversations.some((c) => c.id === state.activeConversationId);
  if (stillActive) {
    renderConversationTabs();
  } else {
    await selectConversation(state.conversations[0].id);
  }
}

function renderConversationTabs() {
  const bar = document.getElementById("conversation-tabs");
  bar.innerHTML = "";

  for (const conversation of state.conversations) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "conversation-tab" + (conversation.id === state.activeConversationId ? " active" : "");
    tab.onclick = () => selectConversation(conversation.id);

    const label = document.createElement("span");
    label.textContent = conversation.name;
    tab.appendChild(label);

    const closeBtn = document.createElement("span");
    closeBtn.className = "conversation-tab-delete";
    closeBtn.textContent = "×";
    // Not a nested <button> (invalid HTML inside the tab's own <button>) — tabindex + keydown
    // keep it keyboard-reachable and activatable like a real button.
    closeBtn.tabIndex = 0;
    closeBtn.setAttribute("role", "button");
    closeBtn.setAttribute("aria-label", `Apagar conversa ${conversation.name}`);
    const deleteConversation = async (e) => {
      e.stopPropagation();
      if (!confirm(`Apagar a conversa "${conversation.name}"? As mensagens dela serão perdidas permanentemente.`)) return;
      const remaining = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "DELETE" }
      );
      state.conversations = remaining;
      if (state.activeConversationId === conversation.id) {
        await selectConversation(remaining[0].id);
      } else {
        renderConversationTabs();
      }
    };
    closeBtn.onclick = deleteConversation;
    closeBtn.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        deleteConversation(e);
      }
    };
    tab.appendChild(closeBtn);
    bar.appendChild(tab);
  }

  const newBtn = document.createElement("button");
  newBtn.type = "button";
  newBtn.id = "new-conversation-btn";
  newBtn.textContent = "+";
  newBtn.setAttribute("aria-label", "Nova conversa");
  newBtn.onclick = async () => {
    const name = prompt("Nome da nova conversa:");
    if (!name || !name.trim()) return;
    const conversation = await api(`/api/groups/${state.activeGroupId}/conversations`, {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    });
    state.conversations.push(conversation);
    await selectConversation(conversation.id);
  };
  bar.appendChild(newBtn);
}

async function selectConversation(conversationId) {
  state.activeConversationId = conversationId;
  state.lastMessageId = 0;
  document.getElementById("message-list").innerHTML = "";
  renderConversationTabs();
  await pollMessages();
}

async function loadMembers(groupId) {
  if (state.agents.length === 0) {
    await loadAgents();
  }
  state.members = await api(`/api/groups/${groupId}/members`);
  renderMembers(groupId);
}

function renderMembers(groupId) {
  const list = document.getElementById("member-list");
  list.innerHTML = "";
  for (const member of state.members) {
    const badge = document.createElement("span");
    badge.className = "member-badge";
    const dot = document.createElement("span");
    dot.className = "member-dot";
    dot.style.background = agentColor(member.name);
    badge.appendChild(dot);
    const name = document.createElement("span");
    name.textContent = member.name;
    badge.appendChild(name);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remover ${member.name} do grupo`);
    removeBtn.onclick = async () => {
      await api(`/api/groups/${groupId}/members/${member.id}`, { method: "DELETE" });
      await loadMembers(groupId);
    };
    badge.appendChild(removeBtn);
    list.appendChild(badge);
  }

  const select = document.getElementById("add-member-select");
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "+ adicionar agente";
  select.appendChild(placeholder);
  const memberIds = new Set(state.members.map((m) => m.id));
  for (const agent of state.agents) {
    if (memberIds.has(agent.id)) continue;
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.name;
    select.appendChild(option);
  }
  select.value = "";
  select.onchange = async () => {
    if (!select.value) return;
    await api(`/api/groups/${groupId}/members`, {
      method: "POST",
      body: JSON.stringify({ agent_id: Number(select.value) }),
    });
    await loadMembers(groupId);
  };
}

function renderMessage(message) {
  const row = document.createElement("div");
  row.className = `message-row ${message.sender_type}`;

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = message.content;

  if (message.sender_type === "agent") {
    const agent = state.agents.find((a) => a.id === message.sender_id);
    const name = agent ? agent.name : "agente";
    const color = agentColor(name);

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.style.background = color;
    avatar.textContent = name.slice(0, 1).toUpperCase();
    row.appendChild(avatar);

    const col = document.createElement("div");
    col.className = "message-col";
    const label = document.createElement("div");
    label.className = "message-sender";
    label.textContent = name;
    label.style.color = color;
    col.appendChild(label);
    col.appendChild(bubble);
    row.appendChild(col);
  } else {
    row.appendChild(bubble);
  }

  if (message.image_path) {
    const img = document.createElement("img");
    img.src = `/api/conversations/${message.conversation_id}/messages/${message.id}/image`;
    img.alt = "Imagem enviada no chat";
    bubble.appendChild(img);
  }

  document.getElementById("message-list").appendChild(row);
}

function updateMessageListEmptyState() {
  const list = document.getElementById("message-list");
  const hasMessages = list.querySelector(".message-row") !== null;
  let placeholder = document.getElementById("message-list-empty");
  if (!hasMessages) {
    if (!placeholder) {
      placeholder = document.createElement("div");
      placeholder.id = "message-list-empty";
      placeholder.className = "empty-state empty-state-inline";
      placeholder.textContent = "Nenhuma mensagem ainda. Escreva algo e mencione @nome-do-agente pra começar.";
      list.appendChild(placeholder);
    }
  } else if (placeholder) {
    placeholder.remove();
  }
}

async function pollMessages() {
  if (!state.activeConversationId) return;
  const messages = await api(
    `/api/conversations/${state.activeConversationId}/messages?since_id=${state.lastMessageId}`
  );
  for (const message of messages) {
    renderMessage(message);
    state.lastMessageId = message.id;
  }
  updateMessageListEmptyState();
  if (messages.length > 0) {
    document.getElementById("message-list").scrollTop = 1e9;
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollMessages, 2000);
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  const list = document.getElementById("agent-list");
  list.innerHTML = "";

  if (state.agents.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum agente ainda — crie o primeiro abaixo.";
    list.appendChild(li);
    return;
  }

  for (const agent of state.agents) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "agent-avatar-dot";
    dot.style.background = agentColor(agent.name);
    li.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
    li.appendChild(label);
    li.onclick = () => startEditingAgent(agent);
    list.appendChild(li);
  }
}

function startEditingAgent(agent) {
  document.getElementById("agent-name").value = agent.name;
  document.getElementById("agent-persona").value = agent.persona_prompt;
  document.getElementById("agent-vision").checked = agent.vision_capable;
  loadModels(agent.model_name);
  state.editingAgentId = agent.id;
  document.getElementById("agent-submit-btn").textContent = "Atualizar agente";
  document.getElementById("cancel-edit-agent-btn").classList.remove("hidden");
  document.getElementById("delete-agent-btn").classList.remove("hidden");
}

function stopEditingAgent() {
  document.getElementById("agent-form").reset();
  state.editingAgentId = null;
  document.getElementById("agent-submit-btn").textContent = "Salvar agente";
  document.getElementById("cancel-edit-agent-btn").classList.add("hidden");
  document.getElementById("delete-agent-btn").classList.add("hidden");
  loadModels();
}

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

async function loadModels(currentValue = "") {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, currentValue, {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => {
      submitButton.disabled = true;
    },
  });
  if (ok) submitButton.disabled = false;
}

async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;

  // Sem onError: ao contrário do formulário de agente, falha ao buscar modelos
  // aqui não deve travar o botão "Salvar" de Configurações (o usuário pode estar
  // justamente tentando corrigir a URL do llama-swap neste mesmo formulário).
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

document.getElementById("sidebar-toggle").onclick = () => {
  const sidebar = document.getElementById("sidebar");
  const opening = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", opening);
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", String(opening));
};

document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
  await loadModels();
};

document.getElementById("nav-settings").onclick = async () => {
  showView("settings");
  await loadSettings();
};

document.getElementById("new-group-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("new-group-name");
  await api("/api/groups", { method: "POST", body: JSON.stringify({ name: input.value }) });
  input.value = "";
  await loadGroups();
};

document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("agent-name").value,
    persona_prompt: document.getElementById("agent-persona").value,
    model_name: document.getElementById("agent-model").value,
    vision_capable: document.getElementById("agent-vision").checked,
  };
  if (state.editingAgentId) {
    await api(`/api/agents/${state.editingAgentId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  } else {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }
  stopEditingAgent();
  await loadAgents();
};

document.getElementById("cancel-edit-agent-btn").onclick = () => {
  stopEditingAgent();
};

document.getElementById("delete-agent-btn").onclick = async () => {
  if (!state.editingAgentId) return;
  const name = document.getElementById("agent-name").value;
  if (!confirm(`Apagar o agente "${name}"?`)) return;
  await api(`/api/agents/${state.editingAgentId}`, { method: "DELETE" });
  stopEditingAgent();
  await loadAgents();
};

document.getElementById("rename-group-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  const currentName = group ? group.name : "";
  const newName = prompt("Novo nome do grupo:", currentName);
  if (!newName || !newName.trim() || newName.trim() === currentName) return;
  await api(`/api/groups/${state.activeGroupId}`, {
    method: "PUT",
    body: JSON.stringify({ name: newName.trim() }),
  });
  await loadGroups();
  const updated = state.groups.find((g) => g.id === state.activeGroupId);
  document.getElementById("channel-header-name").textContent = updated ? `# ${updated.name}` : "";
};

document.getElementById("delete-group-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  const name = group ? group.name : "";
  if (!confirm(`Apagar o grupo "${name}"? Todo o histórico de mensagens será perdido permanentemente.`)) return;
  await api(`/api/groups/${state.activeGroupId}`, { method: "DELETE" });
  state.activeGroupId = null;
  state.activeConversationId = null;
  state.conversations = [];
  document.getElementById("channel-content").classList.add("hidden");
  document.getElementById("channel-empty").classList.remove("hidden");
  await loadGroups();
};

const personaTextarea = document.getElementById("agent-persona");
const generateBtn = document.getElementById("generate-persona-btn");
const generateError = document.getElementById("generate-persona-error");

personaTextarea.addEventListener("input", () => {
  generateBtn.disabled = personaTextarea.value.trim().length === 0;
});

generateBtn.onclick = async () => {
  generateError.textContent = "";
  generateBtn.disabled = true;
  const originalLabel = generateBtn.textContent;
  generateBtn.textContent = "Gerando...";
  personaTextarea.readOnly = true;
  try {
    const data = await api("/api/agents/generate-persona", {
      method: "POST",
      body: JSON.stringify({
        draft: personaTextarea.value,
        agent_name: document.getElementById("agent-name").value,
      }),
    });
    personaTextarea.value = data.persona_prompt;
  } catch (err) {
    generateError.textContent = err.message;
  } finally {
    personaTextarea.readOnly = false;
    generateBtn.textContent = originalLabel;
    generateBtn.disabled = personaTextarea.value.trim().length === 0;
  }
};

document.getElementById("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      llama_swap_base_url: document.getElementById("setting-base-url").value,
      default_vision_model: document.getElementById("setting-vision-model").value,
      max_pending_per_group: document.getElementById("setting-max-pending").value,
      assistant_model: document.getElementById("setting-assistant-model").value,
    }),
  });
};

document.getElementById("image-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  document.getElementById("image-filename").textContent = file ? file.name : "";
});

document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeConversationId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else {
    await api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
};

(async function init() {
  await loadGroups();
  startPolling();
})();
