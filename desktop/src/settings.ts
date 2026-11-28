import { invoke } from "@tauri-apps/api/core";

interface DashboardConfig {
  server: string;
  cliAvailable: boolean;
  hasApiKey: boolean;
}

interface LinkedProjectInfo {
  id: string;
  path: string;
  active: boolean;
}

type TrackerAction = "start" | "pause" | "resume" | "stop" | "sync";
let busy = false;

const byId = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element: ${id}`);
  return element as T;
};

function message(text: string, error = false) {
  const target = byId("message");
  target.textContent = text;
  target.classList.toggle("error", error);
}

function setBusy(value: boolean) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => {
    (button as HTMLButtonElement).disabled = value;
  });
}

async function refreshStatus() {
  try {
    const [config, status] = await Promise.all([
      invoke<DashboardConfig>("dashboard_config"),
      invoke<string>("tracker_status"),
    ]);
    byId("server").textContent = config.server;
    byId("cli").textContent = config.cliAvailable ? "available" : "not found";
    byId("key-state").textContent = config.hasApiKey ? "API key configured" : "no API key";
    byId("connection").textContent = "local CLI ready";
    byId("status").textContent = status;
  } catch (error) {
    byId("connection").textContent = "CLI unavailable";
    byId("status").textContent = String(error);
  }
  void renderProjects();
}

async function renderProjects() {
  const list = byId("project-list");
  const empty = byId("project-empty");
  try {
    const projects = await invoke<LinkedProjectInfo[]>("list_linked_projects");
    list.replaceChildren();
    empty.hidden = projects.length > 0;
    empty.textContent = "No projects linked yet — link a folder above.";
    for (const project of projects) {
      const item = document.createElement("li");
      item.className = "project-row";
      const label = document.createElement("span");
      label.className = "project-label";
      label.textContent = project.active ? `${project.id} (active)` : project.id;
      const path = document.createElement("span");
      path.className = "project-path";
      path.textContent = project.path;
      path.title = project.path;
      const remove = document.createElement("button");
      remove.className = "ghost small";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        if (!window.confirm(`Unlink project "${project.id}"?`)) return;
        void run(
          () => invoke<string>("unlink_project", { id: project.id }),
          `Unlinked ${project.id}`,
        ).then(() => void renderProjects());
      });
      item.append(label, path, remove);
      list.append(item);
    }
  } catch (error) {
    empty.hidden = false;
    empty.textContent = String(error);
  }
}

async function refreshAdapters() {
  const target = byId("adapters");
  try {
    target.textContent = await invoke<string>("adapters_list");
  } catch (error) {
    target.textContent = String(error);
  }
}

async function run(work: () => Promise<unknown>, success: string) {
  if (busy) return;
  setBusy(true);
  message("Working…");
  try {
    const result = await work();
    message(typeof result === "string" && result.trim() ? result : success);
    await refreshStatus();
  } catch (error) {
    message(String(error), true);
  } finally {
    setBusy(false);
  }
}

function onClick(id: string, handler: () => void) {
  byId<HTMLButtonElement>(id).addEventListener("click", handler);
}

onClick("refresh", () => void run(() => invoke<string>("tracker_status"), "Status refreshed"));
onClick("save-key", () => {
  const input = byId<HTMLInputElement>("api-key");
  const key = input.value.trim();
  if (!key) return message("Paste an API key first", true);
  void run(async () => {
    const result = await invoke<string>("set_api_key", { key });
    input.value = "";
    return result;
  }, "API key saved");
});
onClick("clear-key", () => {
  if (!window.confirm("Clear the API key from this machine?")) return;
  void run(() => invoke<string>("clear_api_key"), "API key cleared");
});
onClick("browse", () => {
  void run(async () => {
    const path = await invoke<string | null>("choose_project_folder");
    if (path) byId<HTMLInputElement>("project-path").value = path;
    return path ? "Project folder selected" : "Folder selection cancelled";
  }, "Folder selected");
});
onClick("link-project", () => {
  const path = byId<HTMLInputElement>("project-path").value.trim();
  const project = byId<HTMLInputElement>("project-id").value.trim();
  if (!path) return message("Choose a project folder first", true);
  void run(
    () => invoke<string>("link_project", { path, project: project || null }),
    "Project linked",
  ).then(() => void renderProjects());
});
onClick("refresh-projects", () => void renderProjects());
onClick("refresh-adapters", () => void refreshAdapters());
onClick("install-adapters", () => {
  void run(async () => {
    const result = await invoke<string>("adapters_install", { names: [] });
    await refreshAdapters();
    return result;
  }, "Adapters installed");
});

document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.action as TrackerAction;
    void run(
      () => invoke<string>("tracker_action", { action }),
      `Tracker ${action} completed`,
    );
  });
});

void refreshStatus();
void refreshAdapters();
window.setInterval(() => {
  if (!document.hidden && !busy) void refreshStatus();
}, 5000);
