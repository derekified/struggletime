-- StruggleTime Neovim adapter (Layer-2 enrichment).
-- Emits allowlisted counts to ~/.aitime/agent_events.jsonl — never paths, text, or prompts.
local M = {}

local function events_path()
  local override = vim.env.AITIME_AGENT_EVENTS or vim.env.AITIME_EXEC_LOG
  if override and override ~= "" then
    return override
  end
  local home = vim.env.HOME or vim.fn.expand("~")
  return home .. "/.aitime/agent_events.jsonl"
end

local function debug_enabled()
  local v = tostring(vim.env.STRUGGLETIME_DEBUG or vim.env.AITIME_DEBUG or ""):lower()
  return v == "1" or v == "true" or v == "yes" or v == "on"
end

local function emit(payload)
  local row = {
    ts = os.time(),
    event_type = payload.event_type or "edit",
    category = payload.category or "editor",
    verb = payload.verb or payload.event_type or "edit",
    exit_code = 0,
    editor = "neovim",
    agent = payload.agent or "human",
    op = payload.op,
    files_touched_count = payload.files_touched_count or 0,
    source = "adapter",
  }
  -- Drop any accidental path/content keys before write
  row.path = nil
  row.content = nil
  row.text = nil
  local line = vim.json.encode(row)
  local path = events_path()
  vim.fn.mkdir(vim.fn.fnamemodify(path, ":h"), "p")
  local f = io.open(path, "a")
  if not f then
    return
  end
  f:write(line .. "\n")
  f:close()
  if debug_enabled() then
    vim.notify(
      "[struggletime:debug] " .. vim.json.encode({
        msg = "emit",
        editor = row.editor,
        event_type = row.event_type,
        files_touched_count = row.files_touched_count,
      }),
      vim.log.levels.DEBUG
    )
  end
end

local human_timer = nil
local function schedule_human_edit()
  if human_timer then
    human_timer:stop()
    human_timer:close()
  end
  human_timer = vim.uv.new_timer()
  human_timer:start(1800, 0, vim.schedule_wrap(function()
    emit({
      event_type = "edit",
      verb = "modified",
      op = "modified",
      agent = "human",
      files_touched_count = 1,
    })
  end))
end

function M.setup()
  local grp = vim.api.nvim_create_augroup("StruggleTimeAdapter", { clear = true })

  -- Human typing bursts
  vim.api.nvim_create_autocmd({ "InsertCharPre", "TextChangedI" }, {
    group = grp,
    callback = function()
      schedule_human_edit()
    end,
  })

  -- External / agent / bulk writes (not insert-mode typing)
  vim.api.nvim_create_autocmd({ "TextChanged", "FileChangedShellPost" }, {
    group = grp,
    callback = function()
      if vim.fn.mode():find("i") then
        return
      end
      emit({
        event_type = "edit",
        verb = "modified",
        op = "modified",
        agent = "unknown-agent",
        files_touched_count = 1,
      })
    end,
  })

  vim.api.nvim_create_autocmd("BufWritePost", {
    group = grp,
    callback = function()
      emit({
        event_type = "modified",
        verb = "save",
        op = "modified",
        agent = "human",
        files_touched_count = 1,
      })
    end,
  })

  vim.api.nvim_create_autocmd("FocusGained", {
    group = grp,
    callback = function()
      emit({ event_type = "focus", verb = "focus", files_touched_count = 0 })
    end,
  })

  vim.api.nvim_create_autocmd("FocusLost", {
    group = grp,
    callback = function()
      emit({ event_type = "blur", verb = "blur", files_touched_count = 0 })
    end,
  })
end

return M
