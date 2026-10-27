"""
Move MetaHuman Character assets (what MetaHuman Creator edits; export_metahuman.py reads the head DNA from it)
out of take folders into the character folder (the workspace config's unreal.character_folder, /Game/MetaHumans
without one), so they don't travel with a take when export_animations.py moves
finished takes to Content/Completed.

By default every MetaHuman Character outside DESTINATION is moved; set CHARACTER to move just one, e.g.
"/Game/Completed/Nice_to_Meet_You_tk1/TestMetahuman". References to it are updated (Unreal's redirectors are
fixed up) and everything is saved.

Run in the Unreal Editor: File > Execute Python Script.
"""

import os
import sys

import unreal

CHARACTER = ""                    # one asset path, or "" = every MetaHuman Character outside DESTINATION
DESTINATION = None                # moved into this folder, keeping its name. None = the config's
                                  # unreal.character_folder, else /Game/MetaHumans
SHOW_DIALOG = True


def _class_name(data):
    try:
        return str(data.asset_class_path.asset_name)
    except Exception:
        return str(getattr(data, "asset_class", ""))


def destination():
    """DESTINATION, else unreal.character_folder from the workspace's pipeline_config.json, else /Game/MetaHumans."""
    if DESTINATION:
        return DESTINATION.rstrip("/")
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    for extra in ((here, os.path.join(os.path.dirname(here), "common")) if here else ()):
        if os.path.isdir(extra) and extra not in sys.path:
            sys.path.append(extra)
    try:
        import pipeline_config as pc
        project = os.path.normpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
        cfg = pc.load_config([p for p in (here, project) if p])
        if cfg.get("_config_path"):
            return str(pc.get(cfg, "unreal.character_folder", "/Game/MetaHumans")).rstrip("/")
    except Exception:
        pass
    return "/Game/MetaHumans"


def characters(DESTINATION):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    found = []
    for data in registry.get_assets_by_path("/Game", recursive=True):
        if _class_name(data) != "MetaHumanCharacter":
            continue
        path = f"{data.package_path}/{data.asset_name}"
        if CHARACTER and path != CHARACTER.split(".")[0]:
            continue
        if not CHARACTER and (str(data.package_path) + "/").startswith(DESTINATION.rstrip("/") + "/"):
            continue                                  # already where it belongs
        found.append(path)
    return found


def move_characters():
    DESTINATION = destination()
    lib = unreal.EditorAssetLibrary
    if not lib.does_directory_exist(DESTINATION):
        lib.make_directory(DESTINATION)
    moved, problems = [], []
    for path in characters(DESTINATION):
        name = path.rsplit("/", 1)[1]
        target = f"{DESTINATION}/{name}"
        if lib.does_asset_exist(target):
            problems.append(f"{path}: {target} already exists - rename one of them first")
            continue
        if not lib.rename_asset(path, target):
            problems.append(f"{path}: Unreal couldn't move it (the Output Log says why)")
            continue
        moved.append((path, target))
        unreal.log(f"moved {path} -> {target}")
    if moved:
        # point everything that used the old place at the new one, and drop the redirectors left behind
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        folders = sorted({old.rsplit("/", 1)[0] for old, _ in moved})
        redirectors = []
        for folder in folders:
            for data in registry.get_assets_by_path(folder, recursive=False):
                if _class_name(data) == "ObjectRedirector":
                    asset = data.get_asset()
                    if asset is not None:
                        redirectors.append(asset)
        if redirectors:
            try:
                unreal.AssetToolsHelpers.get_asset_tools().fixup_referencers(redirectors)
            except Exception as err:
                problems.append(f"redirectors not fixed up ({err}); use Fix Up Redirectors on {', '.join(folders)}")
        lib.save_directory(DESTINATION, False, True)
        for folder in folders:
            lib.save_directory(folder, False, True)
    for p in problems:
        unreal.log_warning(p)
    if moved:
        message = "Moved: " + "; ".join(f"{a} -> {b}" for a, b in moved)
    elif not problems:
        message = (f"Nothing to move: no MetaHuman Character outside {DESTINATION}" if not CHARACTER
                   else f"No MetaHuman Character at {CHARACTER}")
    else:
        message = "Nothing moved."
    message += ("\n" + "\n".join(problems)) if problems else ""
    unreal.log(message)
    return message


if __name__ != "move_metahuman_character":
    text = move_characters()
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        try:
            unreal.EditorDialog.show_message("Move MetaHuman Character", text, unreal.AppMsgType.OK)
        except Exception:
            pass
