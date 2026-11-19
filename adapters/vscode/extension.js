// SPDX-License-Identifier: AGPL-3.0-or-later
const vscode = require("vscode"), fs = require("fs"), os = require("os"), path = require("path");
const log = process.env.AITIME_EXEC_LOG || path.join(os.homedir(), ".aitime", "exec.jsonl");
function emit(category, verb) {
  fs.mkdirSync(path.dirname(log), { recursive: true });
  fs.appendFileSync(log, JSON.stringify({ ts: Math.floor(Date.now() / 1000), category, verb, exit_code: 0 }) + "\n");
}
function activate(ctx) {
  let timer;
  ctx.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e => {
    if (e.contentChanges.length === 0) return;
    clearTimeout(timer);
    timer = setTimeout(() => emit(e.document.uri.scheme === "file" ? "editor" : "other", "human_edit"), 2000);
  }));
  ctx.subscriptions.push(vscode.window.onDidChangeWindowState(s => emit("editor", s.focused ? "focus" : "blur")));
}
module.exports = { activate, deactivate: () => {} };
