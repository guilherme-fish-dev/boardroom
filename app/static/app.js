const state = {
  groups: [],
  activeGroupId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
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
  state.lastMessageId = 0;
  document.getElementById("message-list").innerHTML = "";
  const group = state.groups.find((g) => g.id === groupId);
  document.getElementById("channel-header").textContent = group ? `# ${group.name}` : "";
  document.getElementById("channel-empty").classList.add("hidden");
  document.getElementById("channel-content").classList.remove("hidden");
  showView("channel");
  await loadGroups();
  await loadMembers(groupId);
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
    img.src = `/api/groups/${message.group_id}/messages/${message.id}/image`;
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
  if (!state.activeGroupId) return;
  const messages = await api(
    `/api/groups/${state.activeGroupId}/messages?since_id=${state.lastMessageId}`
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
    list.appendChild(li);
  }
}

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

async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-vision-model").value = settings.default_vision_model;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;
  document.getElementById("setting-assistant-model").value = settings.assistant_model;
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
  await api("/api/agents", {
    method: "POST",
    body: JSON.stringify({
      name: document.getElementById("agent-name").value,
      persona_prompt: document.getElementById("agent-persona").value,
      model_name: document.getElementById("agent-model").value,
      vision_capable: document.getElementById("agent-vision").checked,
    }),
  });
  e.target.reset();
  await loadAgents();
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
  if (!state.activeGroupId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/groups/${state.activeGroupId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else {
    await api(`/api/groups/${state.activeGroupId}/messages`, {
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
