# StruggleTime shell hook (bash/zsh) — source this in .bashrc/.zshrc
# Emits ONLY: timestamp, command category, verb, exit code. Never args or raw commands beyond $1.
_aitime_log="${AITIME_EXEC_LOG:-$HOME/.aitime/exec.jsonl}"
mkdir -p "$(dirname "$_aitime_log")"
_aitime_category() {
  case "$1" in
    git) echo git;; claude|codex|aider|opencode|openclaw) echo ai_cli;;
    pytest|jest|vitest|test|go|cargo|npm|pnpm|yarn|make|python|python3|node) echo test_runner;;
    *) echo other;;
  esac
}
_aitime_pre() { _aitime_cmd="$1"; }
_aitime_post() {
  local ec=$?; [ -z "${_aitime_cmd:-}" ] && return
  local name="${_aitime_cmd%% *}"; name="${name##*/}"
  printf '{"ts":%s,"category":"%s","verb":"%s","exit_code":%s}\n' \
    "$(date +%s)" "$(_aitime_category "$name")" "$([ "$name" = git ] && echo "${2:-}" || echo "")" "$ec" >> "$_aitime_log"
  _aitime_cmd=""
}
if [ -n "${ZSH_VERSION:-}" ]; then autoload -Uz add-zsh-hook; add-zsh-hook preexec _aitime_pre; add-zsh-hook precmd _aitime_post
else trap '_aitime_pre "$BASH_COMMAND"' DEBUG; PROMPT_COMMAND="_aitime_post;${PROMPT_COMMAND:-}"; fi
