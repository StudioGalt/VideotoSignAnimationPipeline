import bpy
import json
import os
import sys

# Add the workspace's pose libraries to Blender's asset libraries (Preferences > File Paths > Asset Libraries): each
# folder in Storage (Galtis, MH_rig, Manny_A, Manny_T, metarig_T, Mixamo ...) under its own name. Pose Magic and
# Process Rigs.py save their poses into the library of that name, and the Asset Browser shows them from there.
#
# Run it once in any file (build_metahuman_rig.py runs it too). A library that already has the name is left as it
# is, and so is a folder that is a library under another name; the console lists both, so a library still pointing
# at another workspace is easy to spot (REPOINT = True points those at this workspace's folders).
# The Preferences are kept when Blender closes if Auto-Save Preferences is on (else: Save Preferences).

REPOINT = False      # True = a library of the same name that points at another folder is pointed at Storage/<name>
SKIP = ("Saved",)    # Storage folders that aren't libraries


def storage_folder():
    """<workspace>/Storage from pipeline_config.json (found above the .blend, above this script, or through the
    workspace create_workspace.py noted on this computer), or ""."""
    folders = []
    here = globals().get("__file__")
    if here and os.path.isfile(here):
        folders.append(os.path.dirname(os.path.abspath(here)))
    for text in bpy.data.texts:
        if text.filepath:
            folders.append(os.path.dirname(os.path.abspath(bpy.path.abspath(text.filepath))))
    try:
        with open(os.path.join(os.path.expanduser("~"), ".sign_pipeline.json"), "r", encoding="utf-8") as fh:
            noted = json.load(fh).get("config")
        if noted:
            folders.append(os.path.join(os.path.dirname(noted), "Scripts"))
    except (OSError, ValueError, AttributeError):
        pass
    for folder in folders + [os.path.join(os.path.dirname(f), "common") for f in folders]:
        if os.path.isfile(os.path.join(folder, "pipeline_config.py")) and folder not in sys.path:
            sys.path.append(folder)
    try:
        import pipeline_config as pc
        cfg = pc.load_config([p for p in [bpy.data.filepath] + folders if p])
    except (ImportError, OSError, ValueError):
        return ""
    return pc.project_path(cfg, "storage") if cfg.get("_config_path") else ""


def same(a, b):
    return os.path.normcase(os.path.abspath(bpy.path.abspath(a))).rstrip("\\/") == \
        os.path.normcase(os.path.abspath(b)).rstrip("\\/")


def add_asset_libraries(storage=None, report=print):
    """Add every folder in storage as an asset library of its name. Returns (added, repointed, kept notes)."""
    storage = storage or storage_folder()
    if not storage or not os.path.isdir(storage):
        report(f"  asset libraries: no Storage folder found ({storage or 'no pipeline_config.json'}) - none added")
        return [], [], []
    libraries = bpy.context.preferences.filepaths.asset_libraries
    added, repointed, notes = [], [], []
    for name in sorted(os.listdir(storage)):
        folder = os.path.join(storage, name)
        if not os.path.isdir(folder) or name in SKIP or name.startswith("."):
            continue
        by_name = libraries.get(name)
        by_path = next((lib for lib in libraries if same(lib.path, folder)), None)
        if by_name is not None and same(by_name.path, folder):
            continue                                         # there already
        if by_name is not None:
            if REPOINT:
                by_name.path = folder
                repointed.append(name)
            else:
                notes.append(f"'{name}' points at {bpy.path.abspath(by_name.path)}, not {folder}")
            continue
        if by_path is not None:
            notes.append(f"{folder} is the library '{by_path.name}' (Pose Magic and Process Rigs.py look for '{name}')")
            continue
        try:
            bpy.ops.preferences.asset_library_add(directory=folder + os.sep)
            libraries[-1].name = name
            added.append(name)
        except Exception as err:
            notes.append(f"'{name}' couldn't be added ({err}): add {folder} in Edit > Preferences > File Paths")
    report(f"  asset libraries from {storage}: "
           + (f"added {', '.join(added)}" if added else "nothing to add")
           + (f"; pointed at this workspace: {', '.join(repointed)}" if repointed else ""))
    for note in notes:
        report(f"    {note}" + ("" if REPOINT or "points at" not in note else " (REPOINT = True to change it)"))
    if added or repointed:
        report("    (kept when Blender closes if Auto-Save Preferences is on; else Edit > Preferences > Save Preferences)")
    return added, repointed, notes


if __name__ == "__main__":
    add_asset_libraries()
