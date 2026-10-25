import bpy
import datetime
import json
import os
import re
import shutil
import sys
import time
import zipfile

# Pack a pose library for sharing: the one-pose-per-file library convert_poses_to_metahuman.py (or Blender's
# "Save as pose asset") writes - about 400 KB a pose, mostly the same file overhead every .blend carries - into a
# few compressed .blend files, one per sign (all of "SG ASL Dancer 2025-12-27 P1", P2 ... in
# "SG ASL Dancer 2025-12-27.blend"), and a zip of them. The poses, their names, thumbnails and asset details are kept;
# the Asset Browser shows them as before. A library of thousands of poses comes down to a tenth of its size or less.
#
# The zip is what you share (for example as a file of a GitHub Release). create_workspace.py puts it in place:
#     python create_workspace.py "D:/Sign Workspace" --poses "MH_rig poses.zip"
# (or, with the zip next to create_workspace.py, by itself).
#
# Run it from Blender's Text Editor in any file (it adds nothing to the file), or - quicker for a big library - from a
# command prompt without Blender's window:
#     blender --background --python pack_pose_library.py
# The source library is left as it is.

# --- settings (None = from pipeline_config.json in the workspace) ---
SOURCE = None            # the pose library folder (sub-folders too). None = <Storage>/MH_rig
OUTPUT = None            # the folder the packed files go in. None = <workspace>/Pose Packs/<library name>
LIBRARY = "MH_rig"       # the library's name: the zip is "<LIBRARY> poses.zip", and it unpacks into Storage/<LIBRARY>
GROUP = "SIGN"           # "SIGN" = one file per sign (the name without " P1", " Face P1" ...); "ONE" = a single file;
                         #   "POSE" = one file per pose (compressed)
ZIP = True               # also make "<LIBRARY> poses.zip" next to OUTPUT
ONLY = ""                # only poses whose file name contains this text (case-insensitive); "" = all


def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one."""
    folders = script_folders()
    for folder in folders + [os.path.join(os.path.dirname(f), "common") for f in folders]:
        if os.path.isfile(os.path.join(folder, "pipeline_config.py")) and folder not in sys.path:
            sys.path.append(folder)
    try:
        import pipeline_config
    except ImportError:
        return None, None
    try:
        cfg = pipeline_config.load_config([p for p in [bpy.data.filepath] + folders if p])
    except (OSError, ValueError) as err:
        print(f"  pipeline_config.json couldn't be read: {err}")
        return pipeline_config, None
    return (pipeline_config, cfg) if cfg.get("_config_path") else (pipeline_config, None)


def script_folders():
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
    return list(dict.fromkeys(folders))


SIGN = re.compile(r"^(.*?)(?:\s+Face)?\s+P\d+$", re.I)


def group_of(stem):
    if GROUP == "ONE":
        return f"{LIBRARY} poses"
    if GROUP == "POSE":
        return stem
    match = SIGN.match(stem)
    return match.group(1) if match else stem


def safe(name):
    return "".join("_" if c in '<>:"/\\|?*' else c for c in name).strip() or "poses"


def stem_of(file_name):
    for ending in (".asset.blend", ".blend"):
        if file_name.lower().endswith(ending):
            return file_name[:-len(ending)]
    return file_name


def pack():
    started = time.perf_counter()
    print("[pack_pose_library]")
    pc, cfg = pipeline_settings()
    source = SOURCE or (os.path.join(pc.setting_path(cfg, "storage", ""), LIBRARY) if cfg else "")
    output = OUTPUT or (os.path.join(cfg["_root"], "Pose Packs", LIBRARY) if cfg else "")
    if not source or not output:
        raise RuntimeError("No pipeline_config.json found: set SOURCE and OUTPUT at the top of this script")
    if not os.path.isdir(source):
        raise RuntimeError(f"SOURCE not found: {source}")
    if os.path.normcase(os.path.abspath(output)).startswith(os.path.normcase(os.path.abspath(source)) + os.sep):
        raise RuntimeError("OUTPUT can't be inside SOURCE (the packed files would be packed again next time)")
    print(f"  library:  {source}")
    print(f"  packed:   {output}")

    groups, total_size = {}, 0
    for root, dirs, files in os.walk(source):
        dirs.sort()
        for name in sorted(files):
            if not name.lower().endswith(".blend") or (ONLY and ONLY.lower() not in name.lower()):
                continue
            path = os.path.join(root, name)
            total_size += os.path.getsize(path)
            groups.setdefault(group_of(stem_of(name)), []).append(path)
    if not groups:
        raise RuntimeError(f"No .blend files in {source}")
    if os.path.isdir(output):
        shutil.rmtree(output)                         # a fresh pack each time (the source library is untouched)
    os.makedirs(output)

    poses, written, duplicates, unreadable = 0, [], [], []
    before = set(bpy.data.actions)
    for index, (group, paths) in enumerate(sorted(groups.items())):
        actions = []
        for path in paths:
            try:
                with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
                    dst.actions = list(src.actions)
                for action in dst.actions:
                    if action is None:
                        continue
                    if re.search(r"\.\d{3}$", action.name) and action.name[:-4] in {a.name for a in actions}:
                        duplicates.append(action.name[:-4])   # the same pose name twice: both kept, the second as .001
                    actions.append(action)
            except (OSError, RuntimeError) as err:
                unreadable.append(f"{os.path.basename(path)} ({err})")
        if actions:
            out = os.path.join(output, f"{safe(group)}.blend")
            bpy.data.libraries.write(out, set(actions), path_remap="NONE", fake_user=True, compress=True)
            written.append(out)
            poses += len(actions)
        for action in actions:
            bpy.data.actions.remove(action)
        if (index + 1) % 50 == 0 or index + 1 == len(groups):
            print(f"  {index + 1} of {len(groups)} files written ({poses} poses)")
    for action in [a for a in bpy.data.actions if a not in before]:   # anything that came in with the poses
        bpy.data.actions.remove(action)

    catalogs = os.path.join(source, "blender_assets.cats.txt")   # the library's catalogs, so the poses keep theirs
    if os.path.isfile(catalogs):
        shutil.copy2(catalogs, os.path.join(output, "blender_assets.cats.txt"))
    packed_size = sum(os.path.getsize(p) for p in written)
    manifest = {"library": LIBRARY, "poses": poses, "files": len(written), "packed": datetime.date.today().isoformat(),
                "blender": bpy.app.version_string, "source_size_bytes": total_size, "packed_size_bytes": packed_size}
    with open(os.path.join(output, "pose_pack.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    zip_path = ""
    if ZIP:
        zip_path = os.path.join(os.path.dirname(output), f"{LIBRARY} poses.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:   # the .blend files are compressed already
            for name in sorted(os.listdir(output)):
                zf.write(os.path.join(output, name), f"{LIBRARY}/{name}")

    mb = lambda n: n / (1024 * 1024)
    print("")
    print(f"Packed {poses} pose(s) into {len(written)} file(s) in {(time.perf_counter() - started) / 60:.1f} min: "
          f"{mb(total_size):.1f} MB -> {mb(packed_size):.1f} MB.")
    if zip_path:
        print(f"  zip to share: {zip_path} ({mb(os.path.getsize(zip_path)):.1f} MB)")
        print(f'  install it:   python create_workspace.py "<workspace>" --poses "{os.path.basename(zip_path)}"')
    if duplicates:
        print(f"  {len(duplicates)} pose name(s) appear twice in a file (the second kept as .001): "
              + ", ".join(duplicates[:8]) + (" ..." if len(duplicates) > 8 else ""))
    for line in unreadable[:10]:
        print(f"  couldn't read {line}")
    return zip_path


if __name__ == "__main__":
    pack()
