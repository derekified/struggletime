@tool
extends EditorPlugin

## StruggleTime Godot 4 adapter.
## Writes allowlisted JSONL counts — never paths, script text, or prompts.

const EDITOR_LABEL := "godot"
var _events_path: String = ""
var _last_edit_emit: int = 0
var _was_playing: bool = false

func _enter_tree() -> void:
	_events_path = _resolve_events_path()
	set_process(true)
	var fs := EditorInterface.get_resource_filesystem()
	if fs:
		fs.filesystem_changed.connect(_on_filesystem_changed)
	main_screen_changed.connect(_on_main_screen_changed)

func _exit_tree() -> void:
	set_process(false)
	var fs := EditorInterface.get_resource_filesystem()
	if fs and fs.filesystem_changed.is_connected(_on_filesystem_changed):
		fs.filesystem_changed.disconnect(_on_filesystem_changed)
	if main_screen_changed.is_connected(_on_main_screen_changed):
		main_screen_changed.disconnect(_on_main_screen_changed)

func _resolve_events_path() -> String:
	var override := OS.get_environment("AITIME_AGENT_EVENTS")
	if override.is_empty():
		override = OS.get_environment("AITIME_EXEC_LOG")
	if not override.is_empty():
		return override
	return OS.get_environment("HOME").path_join(".aitime/agent_events.jsonl")

func _debug_enabled() -> bool:
	var v := OS.get_environment("STRUGGLETIME_DEBUG")
	if v.is_empty():
		v = OS.get_environment("AITIME_DEBUG")
	v = v.to_lower()
	return v == "1" or v == "true" or v == "yes" or v == "on"

func _emit(payload: Dictionary) -> void:
	var row := {
		"ts": int(Time.get_unix_time_from_system()),
		"event_type": str(payload.get("event_type", "edit")),
		"category": str(payload.get("category", "editor")),
		"verb": str(payload.get("verb", payload.get("event_type", "edit"))),
		"exit_code": int(payload.get("exit_code", 0)),
		"editor": EDITOR_LABEL,
		"agent": str(payload.get("agent", "human")),
		"files_touched_count": int(payload.get("files_touched_count", 0)),
		"source": "adapter",
	}
	if payload.has("op"):
		row["op"] = payload["op"]
	var dir := _events_path.get_base_dir()
	DirAccess.make_dir_recursive_absolute(dir)
	var f := FileAccess.open(_events_path, FileAccess.READ_WRITE)
	if f == null:
		f = FileAccess.open(_events_path, FileAccess.WRITE)
	if f == null:
		return
	f.seek_end()
	f.store_line(JSON.stringify(row))
	f.close()
	if _debug_enabled():
		print("[struggletime:debug] ", JSON.stringify({
			"msg": "emit",
			"editor": row["editor"],
			"event_type": row["event_type"],
			"files_touched_count": row["files_touched_count"],
		}))

func _on_filesystem_changed() -> void:
	var now := Time.get_ticks_msec()
	if now - _last_edit_emit < 1500:
		return
	_last_edit_emit = now
	_emit({
		"event_type": "edit",
		"verb": "modified",
		"op": "modified",
		"files_touched_count": 1,
	})

func _on_main_screen_changed(_screen: String) -> void:
	_emit({"event_type": "focus", "verb": "focus", "files_touched_count": 0})

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_IN:
		_emit({"event_type": "focus", "verb": "focus", "files_touched_count": 0})
	elif what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		_emit({"event_type": "blur", "verb": "blur", "files_touched_count": 0})

func _process(_delta: float) -> void:
	# play_main_scene / play_current_scene surface as is_playing_scene()
	var playing := EditorInterface.is_playing_scene()
	if playing and not _was_playing:
		_emit({
			"event_type": "play",
			"category": "test_runner",
			"verb": "play",
			"files_touched_count": 0,
		})
	elif not playing and _was_playing:
		_emit({
			"event_type": "stop",
			"category": "test_runner",
			"verb": "stop",
			"files_touched_count": 0,
		})
	_was_playing = playing
