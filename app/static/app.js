const state = {
  groups: [],
  activeGroupId: null,
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
};

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
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  }
}

async function loadGroups() {
  state.groups = await api("/api/groups");
  const list = document.getElementById("group-list");
  list.innerHTML = "";
  for (const group of state.groups) {
    const li = document.createElement("li");
    li.textContent = group.name;
    li.className = group.id === state.activeGroupId ? "active" : "";
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
    const name = document.createElement("span");
    name.textContent = member.name;
    badge.appendChild(name);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
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
  const div = document.createElement("div");
  div.className = `message ${message.sender_type}`;
  div.textContent = message.content;
  if (message.image_path) {
    const img = document.createElement("img");
    img.src = `/api/groups/${message.group_id}/messages/${message.id}/image`;
    div.appendChild(img);
  }
  document.getElementById("message-list").appendChild(div);
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
  for (const agent of state.agents) {
    const li = document.createElement("li");
    li.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
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
}

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

document.getElementById("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      llama_swap_base_url: document.getElementById("setting-base-url").value,
      default_vision_model: document.getElementById("setting-vision-model").value,
      max_pending_per_group: document.getElementById("setting-max-pending").value,
    }),
  });
};

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
