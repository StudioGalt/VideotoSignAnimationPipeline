"""
List the plugins in this Unreal Editor session.

Run in the Unreal Editor: File > Execute Python Script, or in the Output Log's Python console:
    py "<path>/list_plugins.py"

Prints a table to the Output Log and saves it as Saved/plugin_list.txt in the project.
The "name" column is the plugin's internal name (the .uplugin file name), which is what
a script or the .uproject file uses to refer to it.

Enabled state comes from the editor itself when it exposes it to Python
(PluginBlueprintLibrary). Otherwise it is worked out from the .uplugin / .uproject files:
enabled by default, switched on in the .uproject, or needed by another enabled plugin.
"""

import json
import os

import unreal

SHOW_DISABLED = False          # True = list every installed plugin, not just enabled ones
HIGHLIGHT = ("metahuman", "performancecapture", "livelink", "capture", "rigify", "controlrig")
OUTPUT_NAME = "plugin_list.txt"


# ---------------------------------------------------------------- file helpers
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path)) if path else ""


def _plugin_roots():
    """(label, folder) for every place plugins are installed."""
    roots = [("Engine", _full(unreal.Paths.engine_plugins_dir())),
             ("Project", _full(unreal.Paths.project_plugins_dir()))]
    enterprise = getattr(unreal.Paths, "enterprise_plugins_dir", None)
    if enterprise:
        roots.append(("Enterprise", _full(enterprise())))
    return [(label, folder) for label, folder in roots if folder and os.path.isdir(folder)]


def find_installed_plugins():
    """{name: info} for every .uplugin file under the plugin folders."""
    plugins = {}
    for label, root in _plugin_roots():
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("Binaries", "Intermediate", "Content", "Source",
                                                    "Resources", "Config", "Shaders", "Saved")]
            for file in files:
                if not file.endswith(".uplugin"):
                    continue
                path = os.path.join(folder, file)
                data = _read_json(path)
                name = os.path.splitext(file)[0]
                rel = os.path.relpath(path, root).replace("\\", "/")
                source = label
                if label == "Engine" and rel.lower().startswith(("marketplace/", "fab/")):
                    source = "Fab/Marketplace"
                plugins.setdefault(name, {
                    "name": name,
                    "friendly": data.get("FriendlyName") or name,
                    "version": str(data.get("VersionName") or data.get("Version") or ""),
                    "category": data.get("Category") or "",
                    "source": source,
                    "enabled_by_default": data.get("EnabledByDefault", label != "Engine"),
                    "depends": [p.get("Name") for p in data.get("Plugins", [])
                                if p.get("Name") and p.get("Enabled", True)],
                    "path": path,
                })
    return plugins


def project_plugin_settings():
    """{name: enabled} from the "Plugins" list in the .uproject file."""
    uproject = _full(unreal.Paths.get_project_file_path())
    data = _read_json(uproject) if uproject and os.path.isfile(uproject) else {}
    return {p.get("Name"): bool(p.get("Enabled", True)) for p in data.get("Plugins", []) if p.get("Name")}


def enabled_names_from_editor():
    """Enabled plugin names straight from the editor, or None if not exposed to Python."""
    lib = getattr(unreal, "PluginBlueprintLibrary", None)
    getter = getattr(lib, "get_enabled_plugin_names", None) if lib else None
    if getter is None:
        return None
    try:
        return set(str(n) for n in getter())
    except Exception:
        return None


def enabled_names_from_files(plugins, project_settings):
    """Best estimate of the enabled set from the plugin and project files."""
    enabled = {name for name, info in plugins.items()
               if project_settings.get(name, info["enabled_by_default"])}
    enabled |= {name for name, on in project_settings.items() if on}
    changed = True
    while changed:                                   # plugins required by enabled plugins
        changed = False
        for name in list(enabled):
            for dep in plugins.get(name, {}).get("depends", []):
                if dep not in enabled and project_settings.get(dep, True):
                    enabled.add(dep)
                    changed = True
    return enabled


# ---------------------------------------------------------------- report
def main():
    plugins = find_installed_plugins()
    project_settings = project_plugin_settings()
    from_editor = enabled_names_from_editor()
    enabled = from_editor if from_editor is not None else enabled_names_from_files(plugins, project_settings)
    method = "reported by the editor" if from_editor is not None else "worked out from .uplugin/.uproject files"

    installed = len(plugins)
    for name in enabled - set(plugins):              # enabled but no .uplugin found
        plugins[name] = {"name": name, "friendly": name, "version": "", "category": "",
                         "source": "?", "path": ""}

    rows = [p for p in plugins.values() if SHOW_DISABLED or p["name"] in enabled]
    rows.sort(key=lambda p: (p["source"], p["friendly"].lower()))

    w_friendly = max([len(p["friendly"]) for p in rows] + [11])
    w_name = max([len(p["name"]) for p in rows] + [4])
    lines = [
        f"Unreal Engine {unreal.SystemLibrary.get_engine_version()}",
        f"Project: {_full(unreal.Paths.get_project_file_path())}",
        f"{len(enabled)} enabled plugins ({method}), {installed} installed",
        "",
        f"{'':2}{'Plugin':<{w_friendly}}  {'name':<{w_name}}  {'version':<10} {'source':<16} category",
    ]
    for p in rows:
        mark = "* " if any(k in p["name"].lower() for k in HIGHLIGHT) else "  "
        state = "" if p["name"] in enabled else "  (disabled)"
        lines.append(f"{mark}{p['friendly']:<{w_friendly}}  {p['name']:<{w_name}}  "
                     f"{p['version']:<10} {p['source']:<16} {p['category']}{state}")
    lines += ["", "* = MetaHuman / Performance Capture / Live Link / Capture / Control Rig related"]
    if any(p["source"] == "?" for p in rows):
        lines.append("? = enabled, but its .uplugin file wasn't found in the engine or project plugin folders")

    if project_settings:
        lines += ["", "Set in the .uproject file:"]
        for name, on in sorted(project_settings.items()):
            lines.append(f"  {'enabled ' if on else 'disabled'}  {name}")

    report = "\n".join(lines)
    for line in lines:
        unreal.log(line)

    out = os.path.join(_full(unreal.Paths.project_saved_dir()), OUTPUT_NAME)
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        unreal.log(f"Saved to {out}")
    except OSError as err:
        unreal.log_warning(f"Couldn't save {out}: {err}")


main()
