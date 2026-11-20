// SPDX-License-Identifier: AGPL-3.0-or-later
// Layer-2 VS Code / Cursor adapter: editor + op signals only (no paths/content).
// Emits into ~/.aitime/agent_events.jsonl (unified bridge; no MCP).
const vscode = require("vscode"), fs = require("fs"), os = require("os"), path = require("path");
const log = process.env.AITIME_AGENT_EVENTS
  || process.env.AITIME_EXEC_LOG
  || path.join(os.homedir(), ".aitime", "agent_events.jsonl");

function editorLabel() {
  const name = (vscode.env.appName || "").toLowerCase();
  if (name.includes("cursor")) return "cursor";
  if (name.includes("visual studio code") || name.includes("code")) return "vscode";
  return "vscode";
}

function emit(payload) {
  fs.mkdirSync(path.dirname(log), { recursive: true });
  const row = {
    ts: Math.floor(Date.now() / 1000),
    event_type: payload.event_type || payload.op || "edit",
    category: "editor",
    exit_code: 0,
    editor: editorLabel(),
    agent: "human",
    files_touched_count: payload.files_touched_count || 1,
    source: "adapter",
    ...payload,
  };
  // Strip anything that could carry content/paths
  delete row.path; delete row.content; delete row.text;
  fs.appendFileSync(log, JSON.stringify(row) + "\n");
}

function activate(ctx) {
  let timer;
  const ed = editorLabel();
  ctx.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e => {
    if (e.contentChanges.length === 0) return;
    clearTimeout(timer);
    timer = setTimeout(() => emit({
      event_type: "edit", verb: "modified", op: "modified", editor: ed, agent: "human", files_touched_count: 1,
    }), 2000);
  }));
  ctx.subscriptions.push(vscode.workspace.onDidCreateFiles((e) => {
    emit({ event_type: "created", verb: "created", op: "created", editor: ed, agent: "human",
      files_touched_count: (e.files && e.files.length) || 1 });
  }));
  ctx.subscriptions.push(vscode.workspace.onDidDeleteFiles((e) => {
    emit({ event_type: "deleted", verb: "deleted", op: "deleted", editor: ed, agent: "human",
      files_touched_count: (e.files && e.files.length) || 1 });
  }));
  ctx.subscriptions.push(vscode.window.onDidChangeWindowState(s => {
    emit({ event_type: s.focused ? "focus" : "blur", verb: s.focused ? "focus" : "blur", editor: ed, agent: "human",
      files_touched_count: 0 });
  }));
}
module.exports = { activate, deactivate: () => {} };
