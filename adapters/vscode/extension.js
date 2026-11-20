// SPDX-License-Identifier: AGPL-3.0-or-later
// Layer-2 VS Code / Cursor adapter: editor + op signals only (no paths/content).
const vscode = require("vscode"), fs = require("fs"), os = require("os"), path = require("path");
const log = process.env.AITIME_EXEC_LOG || path.join(os.homedir(), ".aitime", "exec.jsonl");

function editorLabel() {
  const name = (vscode.env.appName || "").toLowerCase();
  if (name.includes("cursor")) return "cursor";
  if (name.includes("visual studio code") || name.includes("code")) return "vscode";
  return "vscode";
}

function emit(payload) {
  fs.mkdirSync(path.dirname(log), { recursive: true });
  fs.appendFileSync(log, JSON.stringify({
    ts: Math.floor(Date.now() / 1000),
    category: "editor",
    exit_code: 0,
    editor: editorLabel(),
    agent: "human",
    ...payload,
  }) + "\n");
}

function activate(ctx) {
  let timer;
  const ed = editorLabel();
  ctx.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e => {
    if (e.contentChanges.length === 0) return;
    clearTimeout(timer);
    timer = setTimeout(() => emit({ verb: "modified", op: "modified", editor: ed, agent: "human" }), 2000);
  }));
  ctx.subscriptions.push(vscode.workspace.onDidCreateFiles(() => {
    emit({ verb: "created", op: "created", editor: ed, agent: "human" });
  }));
  ctx.subscriptions.push(vscode.workspace.onDidDeleteFiles(() => {
    emit({ verb: "deleted", op: "deleted", editor: ed, agent: "human" });
  }));
  ctx.subscriptions.push(vscode.window.onDidChangeWindowState(s => {
    emit({ verb: s.focused ? "focus" : "blur", editor: ed, agent: "human" });
  }));
}
module.exports = { activate, deactivate: () => {} };
