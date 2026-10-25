#!/usr/bin/env python3
"""
create_workspace.py - make the working folder for the sign pipeline.

Run with any Python 3.8 or newer (nothing to install):

    python create_workspace.py                                  # asks a few questions
    python create_workspace.py "D:/Sign Workspace" --yes        # the defaults, no questions
    python create_workspace.py "D:/Sign Workspace" --video-source "E:/Videos/Labelled" --yes

It:
  1. makes the workspace folder and the folders in it (see LAYOUT below)
  2. writes pipeline_config.json there: every path and name the scripts need. An existing one is kept;
     only what you type in or pass on the command line changes in it
  3. copies the pipeline's scripts into <workspace>/Scripts (a copy you changed is kept as <name>.bak)
  4. notes the workspace on this computer (~/.sign_pipeline.json), so the scripts find their settings
     wherever they are run from

Run it again at any time: it adds missing folders and updates the scripts, and your settings stay.

Pose packs (made with pack_pose_library.py, e.g. "MH_rig poses.zip" from the project's Releases page) go into
<workspace>/Storage/<library>: give the zip with --poses, or put it next to this script. Poses already there are
left alone, so a newer pack only adds what is new.

LAYOUT (each folder can be moved elsewhere by editing its entry under "paths" in pipeline_config.json):
    <workspace>/
        pipeline_config.json     the settings
        Scripts/                 every pipeline script (Unreal, Blender and the exporters)
        Videos/Labelled/         the labelled source videos, waiting for Unreal
        Videos/Unlabelled/       videos not labelled yet
        Videos/Holding/          videos set aside (the Unreal export only looks in Labelled)
        Capture Media/           Capture Manager's converted footage (Ingest.py)
        Unreal Exports/          phase 1 output: one folder per take (animation, face, video)
        MetaHuman Export/        phase 1 output: the MetaHuman character (meshes, textures, hair)
        MetaHuman/               phase 2: the Blender character file, the neutral pose, the Xsens template FBX
        Storage/                 pose libraries and stored animations (Storage/Galtis, Storage/Mixamo ...)
        Logs/
        <take>/                  made by the Blender side, one per take
"""

import argparse
import filecmp
import glob
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts", "common"))
import pipeline_config as pc  # noqa: E402

# repo folders whose scripts go into <workspace>/Scripts (all in one folder: the scripts find each other there)
SCRIPT_SOURCES = ["scripts/common", "scripts/unreal", "scripts/blender", "scripts/blender/export"]
SKIP_FILES = ("README.md",)

# Storage sub-folders: the Galtis pose library, the stored animations, and one pose library per rig type
# Phase 3 (Galtis and the export to the other rigs) is only in the full copy of the pipeline. Without it, its folders
# (AvatarFolder, Reference Rigs, Ready for Upload and its pose libraries) aren't made.
HAS_PHASE3 = os.path.isfile(os.path.join(HERE, "scripts", "blender", "galtis_import_reference.py"))
PHASE3_FOLDERS = ("avatar", "reference_rigs", "ready_for_upload")
STORAGE_FOLDERS = ["Galtis", "MH_rig", "Animation", "Mixamo", "SOMA", "Reze_T", "Manny_A", "Manny_T", "Quinn_A", "Quinn_T",
                   "MetaHuman_A", "MetaHuman_T", "metarig_A", "metarig_T", "metarig_Gen_A", "metarig_Gen_T"]

if not HAS_PHASE3:
    STORAGE_FOLDERS = ["MH_rig"]


def workspace_folders():
    """The keys of "paths" that are made as folders in this copy of the pipeline."""
    return [k for k in pc.WORKSPACE_FOLDERS if HAS_PHASE3 or k not in PHASE3_FOLDERS]


# (flag, setting, question) - the settings a new user is most likely to change
QUESTIONS = [
    ("video_source", "paths.video_source", "Folder with the labelled source videos"),
    ("unreal_project", "unreal.project", "Your Unreal project (.uproject), for the record (Enter to skip)"),
    ("unreal_exports", "paths.unreal_exports", "Folder for the Unreal take exports"),
    ("metahuman_export", "paths.metahuman_export", "Folder for the MetaHuman character export"),
    ("metahuman_blueprint", "unreal.metahuman_blueprint", "MetaHuman blueprint (Enter = the one in /Game/MetaHumans)"),
]
ADVANCED = [
    ("wip_folder", "unreal.wip_folder", "Unreal folder the takes are exported from"),
    ("completed_folder", "unreal.completed_folder", "Unreal folder exported takes are moved to"),
    ("character_folder", "unreal.character_folder", "Unreal folder for MetaHuman Character assets"),
    ("identity", "unreal.identity", "MetaHuman Identity for every take (\"\" = none)"),
    ("video_action", "video.action", "What happens to a take's video: move, copy or leave"),
]


def parse_args():
    p = argparse.ArgumentParser(description="Make the sign pipeline's working folder (workspace).")
    p.add_argument("root", nargs="?", help="the workspace folder to make or update")
    p.add_argument("--yes", "-y", action="store_true", help="ask nothing: defaults / existing settings / flags")
    p.add_argument("--no-copy", action="store_true", help="don't copy the scripts into the workspace")
    p.add_argument("--no-remember", action="store_true", help="don't note this workspace as the computer's default")
    p.add_argument("--poses", action="append", default=[], metavar="ZIP_OR_FOLDER",
                   help="a pose pack (zip or folder) to put in Storage; a '* poses.zip' next to this script is used too")
    for flag, setting, question in QUESTIONS + ADVANCED:
        p.add_argument("--" + flag.replace("_", "-"), dest=flag, help=question)
    return p.parse_args()


def ask(question, default):
    try:
        answer = input(f"{question} [{default}]: ").strip().strip('"')
    except EOFError:
        answer = ""
    return answer or default


def set_setting(cfg, dotted, value):
    section, key = dotted.split(".", 1)
    cfg.setdefault(section, {})[key] = value


def to_config_path(value, root):
    """A folder inside the workspace is stored relative to it (portable); others in full."""
    full = os.path.abspath(os.path.expanduser(value if os.path.isabs(value) else os.path.join(root, value)))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:            # another drive on Windows
        return full.replace("\\", "/")
    return full.replace("\\", "/") if rel.startswith("..") else rel.replace("\\", "/")


def copy_scripts(dst):
    """Every script into dst. Returns (copied, kept as .bak, name clashes)."""
    os.makedirs(dst, exist_ok=True)
    copied, backed_up, seen, clashes = [], [], {}, []
    for rel in SCRIPT_SOURCES:
        src_dir = os.path.join(HERE, *rel.split("/"))
        if not os.path.isdir(src_dir):
            continue
        for name in sorted(os.listdir(src_dir)):
            s = os.path.join(src_dir, name)
            if (not os.path.isfile(s) or name.startswith(".") or name in SKIP_FILES
                    or name.endswith((".pyc", ".bak", ".blend1"))):
                continue
            if name in seen:
                clashes.append(f"{name} ({seen[name]} and {rel})")
                continue
            seen[name] = rel
            d = os.path.join(dst, name)
            if os.path.abspath(s) == os.path.abspath(d):
                continue
            if os.path.exists(d):
                if filecmp.cmp(s, d, shallow=False):
                    continue
                shutil.copy2(d, d + ".bak")
                backed_up.append(name)
            shutil.copy2(s, d)
            copied.append(name)
    return copied, backed_up, clashes


def pose_packs(given):
    """The pose packs to install: those given, and any '* poses.zip' next to this script."""
    packs = [os.path.abspath(os.path.expanduser(p)) for p in given]
    packs += [p for p in sorted(glob.glob(os.path.join(HERE, "* poses.zip"))) if os.path.abspath(p) not in packs]
    return packs


def install_poses(pack, storage):
    """Put a pose pack in storage/<library>, leaving files already there alone. Returns (library, added, kept) or
    raises ValueError."""
    if os.path.isdir(pack):
        library = os.path.basename(os.path.normpath(pack))
        members = [(os.path.join(root, f), os.path.relpath(os.path.join(root, f), pack))
                   for root, _, files in os.walk(pack) for f in files]
        read = lambda item: open(item, "rb")
    elif zipfile.is_zipfile(pack):
        zf = zipfile.ZipFile(pack)
        names = [n for n in zf.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) == 1 and all("/" in n for n in names):     # "MH_rig/<files>": the library's folder
            library = tops.pop()
            members = [(n, n.split("/", 1)[1]) for n in names]
        else:                                                     # loose files: named after the zip
            library = os.path.basename(pack)[:-len(" poses.zip")] if pack.endswith(" poses.zip") else \
                os.path.splitext(os.path.basename(pack))[0]
            members = [(n, n) for n in names]
        read = zf.open
    else:
        raise ValueError(f"{pack} is neither a folder nor a zip file")
    if library in ("", ".", "..") or any(c in library for c in '/\\:'):
        raise ValueError(f"'{library}' can't be a library folder name")
    target = os.path.join(storage, library)
    if os.path.isdir(target) and not os.path.isfile(os.path.join(target, "pose_pack.json")) and any(
            f.lower().endswith(".blend") for _, _, files in os.walk(target) for f in files):
        raise ValueError(f"Storage/{library} already holds a pose library of its own (from convert_poses_to_metahuman.py)"
                         " - the pack's poses would show twice in the Asset Browser")
    added = kept = 0
    for item, rel in members:
        if ".." in rel.replace("\\", "/").split("/"):
            continue                                              # never outside the library folder
        dest = os.path.join(target, *rel.replace("\\", "/").split("/"))
        if os.path.exists(dest):
            kept += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with read(item) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        added += 1
    return library, added, kept


def main():
    args = parse_args()
    interactive = not args.yes and sys.stdin.isatty()

    root = args.root
    if not root:
        default_root = os.path.join(os.path.expanduser("~"), "Sign Workspace")
        root = ask("Workspace folder", default_root) if interactive else default_root
    root = os.path.abspath(os.path.expanduser(root))
    os.makedirs(root, exist_ok=True)
    config_path = os.path.join(root, pc.CONFIG_FILENAME)

    existing = os.path.isfile(config_path)
    cfg = pc.load_config(config_path) if existing else pc.deep_merge(pc.DEFAULTS, {})
    cfg["_root"] = root
    cfg["config_version"] = pc.CONFIG_VERSION
    print(f"\n{'Updating' if existing else 'Making'} the workspace: {root}\n")

    # settings: a command-line flag > an answer typed now > the existing config / the default
    for flag, setting, question in QUESTIONS + ADVANCED:
        value = getattr(args, flag)
        if value is None and interactive and (flag, setting, question) in QUESTIONS:
            current = pc.get(cfg, setting, "")
            if setting.startswith("paths."):
                current = pc.project_path(cfg, setting.split(".", 1)[1])
            value = ask(question, current)
        if value is None:
            continue
        if setting.startswith("paths."):
            value = to_config_path(value, root)
        elif setting == "unreal.project" and value:
            if "<" in value or ">" in value:
                print(f"  note: '{value}' is a placeholder, not a path - give the full path of your .uproject "
                      "(in File Explorer: right-click it > Copy as path). Not saved.")
                continue
            value = os.path.abspath(os.path.expanduser(value))
            if os.path.isdir(value):                     # the project's folder: the .uproject in it
                found = sorted(f for f in os.listdir(value) if f.lower().endswith(".uproject"))
                if found:
                    value = os.path.join(value, found[0])
            value = value.replace("\\", "/")
            if not os.path.isfile(value):
                print(f"  note: {value} doesn't exist (saved anyway; check the path)")
        elif setting == "video.action" and value not in ("move", "copy", "", "leave"):
            print(f"  note: video action '{value}' isn't move, copy or leave; left as it was")
            continue
        set_setting(cfg, setting, "" if value == "leave" else value)

    # folders
    made = []
    folders = [pc.project_path(cfg, key) for key in workspace_folders()]
    folders += [os.path.join(pc.project_path(cfg, "storage"), name) for name in STORAGE_FOLDERS]
    for folder in folders:
        if not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
            made.append(folder)
    pc.save_config(cfg, config_path)
    remembered = False if args.no_remember else pc.remember_config(config_path)

    scripts = pc.project_path(cfg, "scripts")
    copied, backed_up, clashes = ([], [], []) if args.no_copy else copy_scripts(scripts)
    neutral = pc.project_path(cfg, "neutral_pose")
    shipped = os.path.join(HERE, "config", "neutral_pose.json")
    neutral_note = ""
    if not os.path.isfile(neutral) and os.path.isfile(shipped):   # a starting neutral pose (arms down, hands relaxed)
        os.makedirs(os.path.dirname(neutral), exist_ok=True)
        shutil.copy2(shipped, neutral)
        neutral_note = f"Neutral pose: the default one put in {neutral} (save_neutral_pose.py replaces it with yours)"
    pose_notes = []
    for pack in pose_packs(args.poses):
        try:
            library, added, kept = install_poses(pack, pc.project_path(cfg, "storage"))
            pose_notes.append(f"{os.path.basename(pack)}: {added} file(s) added to Storage/{library}"
                              + (f", {kept} there already" if kept else ""))
        except (OSError, ValueError, zipfile.BadZipFile) as err:
            pose_notes.append(f"{os.path.basename(pack)}: not installed ({err})")

    # report
    print("Folders:")
    for key in workspace_folders():
        folder = pc.project_path(cfg, key)
        print(f"  {'+' if folder in made else ' '} {key:<18} {folder}")
    extra = [f for f in made if f not in [pc.project_path(cfg, k) for k in workspace_folders()]]
    if extra:
        print(f"  + {len(extra)} folder(s) in Storage")
    print(f"\nSettings: {config_path}")
    if remembered:
        print(f"  (noted in {pc.POINTER_FILE}: the scripts find these settings from anywhere on this computer)")
    if copied:
        print(f"Scripts copied / updated in {scripts}: {len(copied)}")
    if backed_up:
        print("Your changed copies were kept as .bak first: " + ", ".join(backed_up))
    if clashes:
        print("WARNING: two scripts share a name, only the first was copied: " + "; ".join(clashes))
    for note in pose_notes:
        print("Poses: " + note)
    if neutral_note:
        print(neutral_note)

    phase3 = (f"""
  Blender (phase 3) - in the Galtis avatar file (put it in {pc.project_path(cfg, 'avatar')})
    9. galtis_import_reference.py, galtis_procedural_fix_mh.py, galtis_finish_animation.py, bake_and_save_galtis.py,
       then MegaExporter.py in the take's Finished file. The rigs it converts to go in
       {pc.project_path(cfg, 'reference_rigs')}""" if HAS_PHASE3 else "")
    template = pc.project_path(cfg, "reference_template")
    template_note = "" if os.path.isfile(template) else (
        f"\n       It needs an Xsens MVN FBX (any; only its skeleton is used) at\n       {template}")
    print(f"""
Next steps
  Unreal (phase 1)
    1. Open your project. Tools > Execute Python Script... (or File > Execute Python Script) runs a script;
       check_plugins.py first, to see that the MetaHuman / Performance Capture plugins are on.
    2. Put the labelled videos in {pc.project_path(cfg, 'video_source')}
       and run Ingest.py: a take folder each under {pc.get(cfg, 'unreal.wip_folder')}.
    3. Run Process.py: it solves the takes (no MetaHuman Identity needed; see unreal.identities to use one).
    4. Run export_metahuman.py once (the character), then export_animations.py for the takes.
       They write to {pc.project_path(cfg, 'unreal_exports')}
       and {pc.project_path(cfg, 'metahuman_export')}
  Blender (phase 2) - Scripting workspace: Text > Open the script from Scripts, then Run Script
    5. In an empty file: build_metahuman_rig.py, once per MetaHuman. It saves
       {pc.project_path(cfg, 'character_blend')}
    6. Optional: a neutral pose of your own (pose the rig as a sign starts and ends, run save_neutral_pose.py);
       a default one is in place.
    7. In that file: import_unreal_take.py, once per take: it saves <workspace>/<take>/<take>.blend.
    8. In the take file: metahuman_procedural_fixes.py, clean up, then export_reference_fbx.py.{template_note}{phase3}
  Settings: change any path or name in {config_path}
""")


if __name__ == "__main__":
    main()
