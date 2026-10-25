"""
Check that the Unreal plugins the pipeline needs are installed and enabled.

Run in the Unreal Editor: File > Execute Python Script. The result is printed to the Output Log
and shown in a message box.

From another script (e.g. at the top of Ingest.py):
    import check_plugins
    if not check_plugins.check_required_plugins():
        raise RuntimeError("Missing plugins - see the Output Log")

The list of plugins comes from "unreal.required_plugins" in pipeline_config.json; without a
config, REQUIRED_PLUGINS below is used. Names are the plugins' internal names (as in the
.uproject file / list_plugins.py).

Enabled state comes from the editor itself when it exposes it to Python
(PluginBlueprintLibrary); otherwise it is worked out from the .uplugin / .uproject files.
"""

import json
import os

import unreal

REQUIRED_PLUGINS = [
    "MetaHuman",
    "MetaHumanBodyTracker",
    "MetaHumanCalibrationProcessing",
    "MetaHumanCharacter",
    "MetaHumanCoreTech",
    "MetaHumanDepthProcessing",
    "MetaHumanGenerator",
    "MetaHumanLiveLink",
    "ModelingToolsEditorMode",
    "PerformanceCaptureCore",
    "PerformanceCaptureToolkit",
    "PerformanceCaptureWorkflow",
]


# ---------------------------------------------------------------- settings
def required_plugins():
    """Plugin names from pipeline_config.json (unreal.required_plugins), else REQUIRED_PLUGINS."""
    try:
        import pipeline_config
        cfg = pipeline_config.load_config(__file__ if "__file__" in globals() else None)
        names = cfg.get("unreal", {}).get("required_plugins")
        if names:
            return list(names), cfg.get("_config_path")
    except ImportError:
        pass
    return list(REQUIRED_PLUGINS), None


# ---------------------------------------------------------------- plugin discovery
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path)) if path else ""


def _installed_plugins():
    """{name: {"engine": bool, "enabled_by_default": bool, "depends": [...], "path": str}}"""
    roots = [("Engine", _full(unreal.Paths.engine_plugins_dir())),
             ("Project", _full(unreal.Paths.project_plugins_dir()))]
    enterprise = getattr(unreal.Paths, "enterprise_plugins_dir", None)
    if enterprise:
        roots.append(("Enterprise", _full(enterprise())))
    skip = {"Binaries", "Intermediate", "Content", "Source", "Resources", "Config", "Shaders", "Saved"}
    plugins = {}
    for label, root in roots:
        if not root or not os.path.isdir(root):
            continue
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for file in files:
                if file.endswith(".uplugin"):
                    path = os.path.join(folder, file)
                    data = _read_json(path)
                    plugins.setdefault(os.path.splitext(file)[0], {
                        "enabled_by_default": data.get("EnabledByDefault", label != "Engine"),
                        "depends": [p.get("Name") for p in data.get("Plugins", [])
                                    if p.get("Name") and p.get("Enabled", True)],
                        "path": path,
                    })
    return plugins


def _project_settings():
    uproject = _full(unreal.Paths.get_project_file_path())
    data = _read_json(uproject) if uproject and os.path.isfile(uproject) else {}
    return {p.get("Name"): bool(p.get("Enabled", True)) for p in data.get("Plugins", []) if p.get("Name")}


def _enabled_plugins(installed, settings):
    """(set of enabled names, how it was determined)."""
    lib = getattr(unreal, "PluginBlueprintLibrary", None)
    getter = getattr(lib, "get_enabled_plugin_names", None) if lib else None
    if getter is not None:
        try:
            return set(str(n) for n in getter()), "reported by the editor"
        except Exception:
            pass
    enabled = {n for n, info in installed.items() if settings.get(n, info["enabled_by_default"])}
    enabled |= {n for n, on in settings.items() if on and n in installed}   # must exist on disk
    changed = True
    while changed:                                    # plugins required by enabled plugins
        changed = False
        for name in list(enabled):
            for dep in installed.get(name, {}).get("depends", []):
                if dep not in enabled and settings.get(dep, True):
                    enabled.add(dep)
                    changed = True
    return enabled, "worked out from .uplugin/.uproject files"


# ---------------------------------------------------------------- check
def check_required_plugins(show_dialog=False):
    """Log the state of each required plugin. Returns True when all are installed and enabled."""
    names, config_path = required_plugins()
    installed = _installed_plugins()
    settings = _project_settings()
    enabled, method = _enabled_plugins(installed, settings)

    ok, disabled, missing = [], [], []
    for name in names:
        if name in enabled:
            ok.append(name)
        elif name in installed:
            disabled.append(name)
        else:
            missing.append(name)

    width = max(len(n) for n in names) if names else 10
    unreal.log(f"Required plugins ({method})"
               + (f" - list from {config_path}" if config_path else ""))
    for name in names:
        if name in ok:
            unreal.log(f"  OK            {name}")
        elif name in disabled:
            unreal.log_warning(f"  NOT ENABLED   {name:<{width}}  installed - enable it in Edit > Plugins")
        else:
            unreal.log_error(f"  NOT INSTALLED {name:<{width}}  install it (Fab / Epic Games Launcher)")

    all_ok = not disabled and not missing
    if all_ok:
        summary = f"All {len(names)} required plugins are installed and enabled."
        unreal.log(summary)
    else:
        parts = []
        if disabled:
            parts.append("Installed but not enabled (Edit > Plugins, then restart the editor):\n  "
                         + "\n  ".join(disabled))
        if missing:
            parts.append("Not installed:\n  " + "\n  ".join(missing))
        summary = f"{len(ok)} of {len(names)} required plugins ready.\n\n" + "\n\n".join(parts)
        unreal.log_warning(summary.replace("\n", " "))

    if show_dialog:
        dialog = getattr(unreal, "EditorDialog", None)
        if dialog is not None:
            try:
                dialog.show_message("Required plugins", summary, unreal.AppMsgType.OK)
            except Exception:
                pass
    return all_ok


# Run the check when executed as a script (File > Execute Python Script), not when imported
if __name__ != "check_plugins":
    check_required_plugins(show_dialog=True)
