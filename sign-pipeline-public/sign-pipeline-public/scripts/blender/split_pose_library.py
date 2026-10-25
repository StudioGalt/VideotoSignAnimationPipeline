import bpy
import json
import os
import re
import shutil
import sys
import time

# Split a pose library in two: BASIC (fingerspelling and numbers) and ADVANCED (everything else), each a folder in
# Storage and an asset library of its own in the Asset Browser, so each can be browsed - and packed and shared with
# pack_pose_library.py - on its own.
#
# Which poses are Basic: those whose name, once the "SG"/"SGM"/"ASL" words in front, a date, a variant in brackets and
# a pose number are left out, is one letter or a number:
#     "SGM ASL A", "SGM ASL 7", "SGM ASL 21", "SGM ASL C (Soft)", "SGM ASL C (Hard)", "SGM ASL Z P1" ... "SGM ASL Z P4",
#     "SG ASL Z 2 2026-10-12"                                             -> Basic
#     "SGM ASL Thank You", "SG ASL Dancer 2025-12-27 P1"                  -> Advanced
# BASIC_NAMES / ADVANCED_NAMES settle any pose the rule gets wrong.
#
# Run it from Blender's Text Editor in any file (nothing is added to the file). With DRY_RUN = True (the first time)
# it only lists the split, in the console and in <workspace>/Logs/pose_split.txt; look it over, then set
# DRY_RUN = False and run it again to move the files. convert_poses_to_metahuman.py sorts the poses it converts by the
# same rule (it reads this script for it), straight into the two libraries; this script is for a library made before.

# --- settings (None = from pipeline_config.json in the workspace) ---
SOURCE = "Body_Poses"            # the library to split: an asset library's name or a folder (Storage/<this> first)
BASIC = "Body_Poses_Basic"       # Storage/<this>, and the asset library of that name
ADVANCED = "Body_Poses_Advanced"
DRY_RUN = True                   # True = only list the split; False = move the poses
COPY = False                     # True = copy the poses, leaving SOURCE as it is; False = move them
BASIC_NAMES = []                 # poses that are Basic whatever the rule says, e.g. ["SGM ASL I Love You"]
ADVANCED_NAMES = []              # poses that are Advanced whatever the rule says
LEAD_WORDS = ("SG", "SGM", "ASL")   # words in front of the sign's name that are left out


def storage_and_logs():
    """(Storage folder, Logs folder) from pipeline_config.json, or ("", "")."""
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
        return "", ""
    if not cfg.get("_config_path"):
        return "", ""
    return pc.project_path(cfg, "storage"), pc.project_path(cfg, "logs")


def library_folder(text, storage):
    """A folder for `text`: itself when it is one, Storage/<text>, or the folder of the asset library of that name."""
    if os.path.isdir(text):
        return text
    if storage and os.path.isdir(os.path.join(storage, text)):
        return os.path.join(storage, text)
    key = lambda s: "".join(s.lower().split())
    for lib in bpy.context.preferences.filepaths.asset_libraries:
        if key(lib.name) == key(text):
            return os.path.normpath(bpy.path.abspath(lib.path))
    raise RuntimeError(f"'{text}' is neither a folder, a folder in {storage or 'Storage'}, nor an asset library")


def sign_name(pose):
    """The sign's own name in a pose's name: 'SGM ASL C (Soft) P2' -> 'C', 'SG ASL Dancer 2025-12-27 P1' -> 'Dancer'."""
    s = re.sub(r"\s+(?:Face\s+)?P\d+$", "", pose.strip(), flags=re.I)            # Pose Magic's P1, P2 ...
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)                                    # a variant: (Soft), (Hard)
    s = re.sub(r"\b\d{4}[-_ ]\d{1,2}[-_ ]\d{1,2}\b", " ", s)                       # a date
    words = s.replace("_", " ").split()
    while words and words[0].upper() in LEAD_WORDS:
        words.pop(0)
    return " ".join(words)


BASIC_SIGN = re.compile(r"^(?:[A-Za-z]|\d{1,3})(?:\s*-?\s*\d{1,2})?$")   # one letter or a number ("Z 2": a Z pose)


def is_basic(pose):
    if pose in BASIC_NAMES:
        return True
    if pose in ADVANCED_NAMES:
        return False
    return bool(BASIC_SIGN.match(sign_name(pose)))


def pose_files(folder):
    found = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        found += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(".blend")]
    return found


def poses_in(path):
    try:
        with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, _dst):
            return list(src.actions)
    except (OSError, RuntimeError):
        return None


def unique(path):
    """`path`, or '<name> (2).asset.blend' ... when a file of that name is there already."""
    ext = ".asset.blend" if path.lower().endswith(".asset.blend") else os.path.splitext(path)[1]
    stem, n, candidate = path[:-len(ext)], 2, path
    while os.path.exists(candidate):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return candidate


def add_library(name, folder):
    libraries = bpy.context.preferences.filepaths.asset_libraries
    for lib in libraries:
        if os.path.normcase(os.path.abspath(bpy.path.abspath(lib.path))).rstrip("\\/") == \
                os.path.normcase(os.path.abspath(folder)).rstrip("\\/"):
            return f"'{lib.name}' is already {folder}"
    try:
        bpy.ops.preferences.asset_library_add(directory=folder + os.sep)
        libraries[-1].name = name
        return f"added the asset library '{name}' ({folder})"
    except Exception as err:
        return f"couldn't add '{name}' ({err}): add {folder} in Edit > Preferences > File Paths > Asset Libraries"


def write_split(path, poses, folders):
    """A file holding poses of both kinds: each pose written to its own file in its folder. Returns the files."""
    with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
        dst.actions = list(src.actions)
    written = []
    try:
        for action in [a for a in dst.actions if a is not None]:
            safe = "".join("_" if c in '<>:"/\\|?*' else c for c in action.name)
            out = unique(os.path.join(folders[is_basic(action.name)], f"{safe}.asset.blend"))
            bpy.data.libraries.write(out, {action}, path_remap="NONE", fake_user=True, compress=True)
            written.append(out)
    finally:
        for action in [a for a in dst.actions if a is not None]:
            bpy.data.actions.remove(action)
    return written


def split():
    started = time.perf_counter()
    print("[split_pose_library]" + ("  DRY RUN - nothing is moved" if DRY_RUN else ""))
    storage, logs = storage_and_logs()
    source = library_folder(SOURCE, storage)
    folders = {True: os.path.join(storage, BASIC) if storage else "", False: os.path.join(storage, ADVANCED) if storage else ""}
    if not storage:
        raise RuntimeError("No pipeline_config.json found, so the Storage folder isn't known (run create_workspace.py)")
    print(f"  library:  {source}")
    print(f"  Basic:    {folders[True]}")
    print(f"  Advanced: {folders[False]}")
    rows, mixed, unreadable = [], [], []
    for path in pose_files(source):
        names = poses_in(path)
        if names is None:
            unreadable.append(os.path.basename(path))
            continue
        kinds = {is_basic(n) for n in names}
        rows.append((path, names, kinds))
        if len(kinds) > 1:
            mixed.append(os.path.basename(path))
    basic = sorted(n for _, names, _ in rows for n in names if is_basic(n))
    advanced = sorted(n for _, names, _ in rows for n in names if not is_basic(n))
    print(f"  {len(basic)} Basic and {len(advanced)} Advanced pose(s) in {len(rows)} file(s)")
    print("  Basic: " + ", ".join(basic[:200]) + (" ..." if len(basic) > 200 else ""))
    print("  Advanced, e.g.: " + ", ".join(advanced[:25]) + (" ..." if len(advanced) > 25 else ""))
    if logs:
        os.makedirs(logs, exist_ok=True)
        report = os.path.join(logs, "pose_split.txt")
        with open(report, "w", encoding="utf-8") as fh:
            fh.write(f"Split of {source} ({time.strftime('%Y-%m-%d %H:%M')})\n\n")
            fh.write(f"BASIC ({len(basic)}) -> {folders[True]}\n")
            fh.writelines(f"  {n}    [sign: {sign_name(n)}]\n" for n in basic)
            fh.write(f"\nADVANCED ({len(advanced)}) -> {folders[False]}\n")
            fh.writelines(f"  {n}    [sign: {sign_name(n)}]\n" for n in advanced)
        print(f"  the whole list: {report}")
    if DRY_RUN:
        print("Look the list over (BASIC_NAMES / ADVANCED_NAMES correct the rule), then set DRY_RUN = False and run "
              "this again.")
        return
    for folder in folders.values():
        os.makedirs(folder, exist_ok=True)
    moved = 0
    for path, names, kinds in rows:
        if len(kinds) > 1:                            # a file with both kinds: each pose to its own file
            moved += len(write_split(path, names, folders))
            if not COPY:
                os.remove(path)
            continue
        target = unique(os.path.join(folders[kinds.pop()], os.path.relpath(path, source)))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        (shutil.copy2 if COPY else shutil.move)(path, target)
        moved += len(names)
    catalogs = os.path.join(source, "blender_assets.cats.txt")
    for folder in folders.values():                   # the catalogs, so the poses keep theirs in either library
        if os.path.isfile(catalogs) and not os.path.isfile(os.path.join(folder, "blender_assets.cats.txt")):
            shutil.copy2(catalogs, os.path.join(folder, "blender_assets.cats.txt"))
    if not COPY:
        for root, dirs, files in os.walk(source, topdown=False):   # empty sub-folders left behind
            if root != source and not os.listdir(root):
                os.rmdir(root)
    notes = [add_library(BASIC, folders[True]), add_library(ADVANCED, folders[False])]
    print(f"{'Copied' if COPY else 'Moved'} {moved} pose(s) in {time.perf_counter() - started:.1f} s"
          + (f"; {len(mixed)} file(s) held both kinds and were split pose by pose" if mixed else "") + ".")
    for note in notes:
        print(f"  {note}")
    left = [f for f in pose_files(source)]
    if not COPY and not left:
        print(f"  {source} is empty now: its asset library ('{SOURCE}') can be removed in Edit > Preferences > "
              "File Paths > Asset Libraries - or keep it as the inbox convert_poses_to_metahuman.py saves new poses "
              "into, and run this again after converting")
    print("  The Preferences are kept when Blender closes if Auto-Save Preferences is on. Refresh the Asset Browser.")
    for name in unreadable[:10]:
        print(f"  couldn't read {name}")


if __name__ == "__main__":
    split()
