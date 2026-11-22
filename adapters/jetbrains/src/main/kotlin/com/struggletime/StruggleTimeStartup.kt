package com.struggletime

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.editor.event.DocumentEvent
import com.intellij.openapi.editor.event.DocumentListener
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.intellij.openapi.wm.WindowManager
import java.awt.event.WindowAdapter
import java.awt.event.WindowEvent
import java.io.File
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread

/**
 * Minimal JetBrains IDE adapter (IntelliJ / PyCharm / CLion / WebStorm /
 * GoLand / RustRover / Rider / Android Studio).
 *
 * Emits allowlisted JSONL rows — never document text, paths, or prompts.
 */
class StruggleTimeStartup : ProjectActivity {
    override suspend fun execute(project: Project) {
        ApplicationManager.getApplication().invokeLater {
            StruggleTimeBridge.install(project)
        }
    }
}

object StruggleTimeBridge {
    private val lastHumanEmit = AtomicLong(0)
    private val lastBulkEmit = AtomicLong(0)

    fun install(project: Project) {
        com.intellij.openapi.editor.EditorFactory.getInstance().eventMulticaster.addDocumentListener(
            object : DocumentListener {
                override fun documentChanged(event: DocumentEvent) {
                    val inserted = event.newFragment.length
                    val deleted = event.oldFragment.length
                    val multi = inserted > 80 || deleted > 80 || (inserted + deleted) > 120
                    val now = System.currentTimeMillis()
                    if (multi) {
                        if (now - lastBulkEmit.get() < 1500) return
                        lastBulkEmit.set(now)
                        emit(
                            mapOf(
                                "event_type" to "edit",
                                "verb" to "modified",
                                "op" to "modified",
                                "agent" to "unknown-agent",
                                "files_touched_count" to 1,
                            )
                        )
                    } else {
                        if (now - lastHumanEmit.get() < 1800) return
                        lastHumanEmit.set(now)
                        emit(
                            mapOf(
                                "event_type" to "edit",
                                "verb" to "modified",
                                "op" to "modified",
                                "agent" to "human",
                                "files_touched_count" to 1,
                            )
                        )
                    }
                }
            },
            project,
        )

        val frame = WindowManager.getInstance().getFrame(project) ?: return
        frame.addWindowFocusListener(object : WindowAdapter() {
            override fun windowGainedFocus(e: WindowEvent?) {
                emit(mapOf("event_type" to "focus", "verb" to "focus", "files_touched_count" to 0))
            }

            override fun windowLostFocus(e: WindowEvent?) {
                emit(mapOf("event_type" to "blur", "verb" to "blur", "files_touched_count" to 0))
            }
        })
    }

    private fun eventsPath(): File {
        val override = System.getenv("AITIME_AGENT_EVENTS")
            ?: System.getenv("AITIME_EXEC_LOG")
        if (!override.isNullOrBlank()) return File(override)
        val home = System.getProperty("user.home")
        return File(home, ".aitime/agent_events.jsonl")
    }

    private fun debugEnabled(): Boolean {
        val v = (System.getenv("STRUGGLETIME_DEBUG") ?: System.getenv("AITIME_DEBUG") ?: "")
            .trim().lowercase()
        return v == "1" || v == "true" || v == "yes" || v == "on"
    }

    fun emit(payload: Map<String, Any?>) {
        thread(name = "struggletime-emit", isDaemon = true) {
            try {
                val row = linkedMapOf<String, Any?>(
                    "ts" to (System.currentTimeMillis() / 1000L),
                    "event_type" to (payload["event_type"] ?: "edit"),
                    "category" to (payload["category"] ?: "editor"),
                    "verb" to (payload["verb"] ?: payload["event_type"] ?: "edit"),
                    "exit_code" to 0,
                    "editor" to "jetbrains",
                    "agent" to (payload["agent"] ?: "human"),
                    "files_touched_count" to (payload["files_touched_count"] ?: 0),
                    "source" to "adapter",
                )
                payload["op"]?.let { row["op"] = it }
                row.remove("path"); row.remove("content"); row.remove("text")
                val dest = eventsPath()
                dest.parentFile?.mkdirs()
                dest.appendText(jsonObject(row) + "\n")
                if (debugEnabled()) {
                    System.err.println(
                        "[struggletime:debug] " + jsonObject(
                            mapOf(
                                "msg" to "emit",
                                "editor" to "jetbrains",
                                "event_type" to row["event_type"],
                                "files_touched_count" to row["files_touched_count"],
                            )
                        )
                    )
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun jsonObject(map: Map<String, Any?>): String {
        val parts = map.entries.joinToString(",") { (k, v) ->
            val encoded = when (v) {
                null -> "null"
                is Number, is Boolean -> v.toString()
                else -> "\"" + v.toString().replace("\\", "\\\\").replace("\"", "\\\"") + "\""
            }
            "\"$k\":$encoded"
        }
        return "{$parts}"
    }
}
