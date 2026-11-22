// StruggleTime Unity Editor adapter (Layer-2 enrichment).
// Allowlisted focus / save / play-mode counts only — never asset contents or paths.
using System;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class StruggleTimeTracker
{
    const string EditorLabel = "unity";
    static double _lastFocusEmit;
    static double _lastSaveEmit;

    static StruggleTimeTracker()
    {
        EditorApplication.update += OnUpdate;
        EditorApplication.playModeStateChanged += OnPlayMode;
    }

    static string EventsPath()
    {
        var overridePath = Environment.GetEnvironmentVariable("AITIME_AGENT_EVENTS")
            ?? Environment.GetEnvironmentVariable("AITIME_EXEC_LOG");
        if (!string.IsNullOrEmpty(overridePath))
            return overridePath;
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return Path.Combine(home, ".aitime", "agent_events.jsonl");
    }

    static bool DebugEnabled()
    {
        var v = (Environment.GetEnvironmentVariable("STRUGGLETIME_DEBUG")
            ?? Environment.GetEnvironmentVariable("AITIME_DEBUG")
            ?? "").Trim().ToLowerInvariant();
        return v == "1" || v == "true" || v == "yes" || v == "on";
    }

    static void Emit(string eventType, string verb, string category = "editor", string op = null, int files = 0, string agent = "human")
    {
        try
        {
            var path = EventsPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ".");
            var sb = new StringBuilder(160);
            sb.Append("{\"ts\":").Append(DateTimeOffset.UtcNow.ToUnixTimeSeconds());
            sb.Append(",\"event_type\":\"").Append(eventType).Append('"');
            sb.Append(",\"category\":\"").Append(category).Append('"');
            sb.Append(",\"verb\":\"").Append(verb).Append('"');
            sb.Append(",\"exit_code\":0");
            sb.Append(",\"editor\":\"").Append(EditorLabel).Append('"');
            sb.Append(",\"agent\":\"").Append(agent).Append('"');
            if (!string.IsNullOrEmpty(op))
                sb.Append(",\"op\":\"").Append(op).Append('"');
            sb.Append(",\"files_touched_count\":").Append(files);
            sb.Append(",\"source\":\"adapter\"}");
            File.AppendAllText(path, sb.ToString() + "\n");
            if (DebugEnabled())
                Debug.Log("[struggletime:debug] {\"msg\":\"emit\",\"editor\":\"unity\",\"event_type\":\"" + eventType + "\",\"files_touched_count\":" + files + "}");
        }
        catch
        {
            // Never throw into the Unity Editor loop.
        }
    }

    static void OnUpdate()
    {
        var now = EditorApplication.timeSinceStartup;
        if (now - _lastFocusEmit < 2.0)
            return;
        // Pulse focus while the editor is the active application.
        if (EditorApplication.isFocused)
        {
            _lastFocusEmit = now;
            Emit("focus", "focus", files: 0);
        }
    }

    static void OnPlayMode(PlayModeStateChange state)
    {
        if (state == PlayModeStateChange.EnteredPlayMode)
            Emit("play", "play", category: "test_runner");
        else if (state == PlayModeStateChange.ExitingPlayMode)
            Emit("stop", "stop", category: "test_runner");
    }

    class SaveHook : AssetModificationProcessor
    {
        static string[] OnWillSaveAssets(string[] paths)
        {
            var now = EditorApplication.timeSinceStartup;
            if (now - _lastSaveEmit < 1.0)
                return paths;
            _lastSaveEmit = now;
            // Count only — never echo asset paths into the bridge.
            var n = paths != null ? Math.Min(paths.Length, 50) : 1;
            Emit("modified", "save", op: "modified", files: Math.Max(1, n));
            return paths;
        }
    }
}
