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
#     python create_workspace.py "D:/Sign Workspace" --poses "Body_Poses_Basic poses.zip"
# (or, with the zip next to create_workspace.py, by itself).
#
# Run it from Blender's Text Editor in any file (it adds nothing to the file), or - quicker for a big library - from a
# command prompt without Blender's window:
#     blender --background --python pack_pose_library.py
# The source library is left as it is.

# --- settings (None = from pipeline_config.json in the workspace) ---
SOURCE = None            # the pose library: a folder (sub-folders too) or an asset library's name. None = <Storage>/<LIBRARY>
OUTPUT = None            # the folder the packed files go in. None = <workspace>/Pose Packs/<library name>
LIBRARY = ["Body_Poses_Basic", "Body_Poses_Advanced", "Face_Poses"]   # the library's name: the zip is "<LIBRARY> poses.zip", and it unpacks into Storage/<LIBRARY>.
                         #   A list packs each in turn, e.g. ["Body_Poses_Basic", "Body_Poses_Advanced"] (with SOURCE
                         #   and OUTPUT left as None: Storage/<each> into Pose Packs/<each>)
GROUP = "SIGN"           # "SIGN" = one file per sign (the name without " P1", " Face P1" ...); "ONE" = a single file;
                         #   "POSE" = one file per pose (compressed)
ZIP = True               # also make "<LIBRARY> poses.zip" next to OUTPUT
ONLY = ""                # only poses whose file name contains this text (case-insensitive); "" = all
PICK = ""                # only the poses named as in this library (a folder, or an asset library's name, e.g.
                         #   "Favorite Poses"): to pack the converted (MetaHuman rig) versions of your favourite Galtis poses. "" = all
KIND = "AUTO"            # "AUTO" = face poses for a library with "Face" in its name, body poses for any other;
                         #   "BODY" = body poses only (no "<sign> Face P1" ..., no pose that only moves the face board);
                         #   "FACE" = face poses only; "ALL" = both


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


def library_folder(text):
    """A folder for `text`: the folder itself when it is one (or a .blend), else the folder of the Asset Browser
    library of that name (Edit > Preferences > File Paths > Asset Libraries; case and spaces don't matter).
    Raises with the libraries there are, and the closest name, when there is none."""
    import difflib
    if not text:
        return text
    if os.path.exists(text) or os.path.exists(text + ".blend"):
        return text if os.path.exists(text) else text + ".blend"
    key = lambda s: "".join(s.lower().split())
    libraries = {lib.name: bpy.path.abspath(lib.path) for lib in bpy.context.preferences.filepaths.asset_libraries}
    for name, path in libraries.items():
        if key(name) == key(text):
            return os.path.normpath(path)
    close = difflib.get_close_matches(key(text), [key(n) for n in libraries], n=1, cutoff=0.6)
    guess = next((n for n in libraries if close and key(n) == close[0]), None)
    raise RuntimeError(f"'{text}' is neither a folder nor an asset library."
                       + (f" Did you mean '{guess}'?" if guess else "")
                       + f" Asset libraries: {', '.join(sorted(libraries)) or 'none'}")


FACE_POSE = re.compile(r"\sFace\s+P\d+$", re.I)    # Pose Magic's face poses: "<sign> Face P1" ...


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


def pose_names(folder):
    """The names of every pose asset (action) in a folder of .blend files, or in one .blend."""
    files = [folder] if os.path.isfile(folder) else [os.path.join(r, f) for r, _, fs in os.walk(folder)
                                                     for f in fs if f.lower().endswith(".blend")]
    names = set()
    for path in files:
        try:
            with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, _dst):
                names.update(src.actions)
        except (OSError, RuntimeError):
            pass
    return names


def is_face_pose(action):
    """A face pose: named '<sign> Face P1' ..., or every channel on a face board control (CTRL_ bones)."""
    if FACE_POSE.search(action.name):
        return True
    paths = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in strip.channelbags:
                paths += [fc.data_path for fc in bag.fcurves]
    bones = {m.group(1) for m in (re.match(r'pose\.bones\["([^"]+)"\]', p) for p in paths) if m}
    return bool(bones) and all(b.startswith("CTRL_") for b in bones)


def pack():
    started = time.perf_counter()
    print("[pack_pose_library]")
    pc, cfg = pipeline_settings()
    source = SOURCE or (os.path.join(pc.setting_path(cfg, "storage", ""), LIBRARY) if cfg else "")
    output = OUTPUT or (os.path.join(cfg["_root"], "Pose Packs", LIBRARY) if cfg else "")
    if not source or not output:
        raise RuntimeError("No pipeline_config.json found: set SOURCE and OUTPUT at the top of this script")
    source = library_folder(source)
    if not os.path.isdir(source):
        raise RuntimeError(f"SOURCE not found: {source}")
    wanted = None
    if PICK:
        pick = library_folder(PICK)
        wanted = pose_names(pick)
        print(f"  picked:   the {len(wanted)} pose(s) named as in {pick}")
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
    left_out, found_names = 0, set()
    kind = KIND if KIND != "AUTO" else ("FACE" if "face" in LIBRARY.lower() else "BODY")
    before = set(bpy.data.actions)
    for index, (group, paths) in enumerate(sorted(groups.items())):
        actions = []
        for path in paths:
            try:
                with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
                    dst.actions = [a for a in src.actions if wanted is None or a in wanted]
                for action in dst.actions:
                    if action is None:
                        continue
                    if kind != "ALL" and is_face_pose(action) != (kind == "FACE"):
                        left_out += 1
                        bpy.data.actions.remove(action)
                        continue
                    found_names.add(action.name)
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
    if left_out:
        print(f"  {left_out} {'face' if kind == 'BODY' else 'body'} pose(s) left out (KIND = \"{KIND}\")")
    if wanted is not None:
        missing = sorted(n for n in wanted if n not in found_names and not FACE_POSE.search(n))
        if missing and kind != "FACE":
            print(f"  {len(missing)} picked pose(s) aren't in {source} yet - convert them first "
                  "(convert_poses_to_metahuman.py, with SOURCE = the PICK library): "
                  + ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""))
    if duplicates:
        print(f"  {len(duplicates)} pose name(s) appear twice in a file (the second kept as .001): "
              + ", ".join(duplicates[:8]) + (" ..." if len(duplicates) > 8 else ""))
    for line in unreadable[:10]:
        print(f"  couldn't read {line}")
    return zip_path


def pack_all():
    global LIBRARY
    libraries = list(LIBRARY) if isinstance(LIBRARY, (list, tuple)) else [LIBRARY]
    if len(libraries) > 1 and (SOURCE or OUTPUT):
        raise RuntimeError("With several libraries in LIBRARY, leave SOURCE and OUTPUT as None (each packs Storage/<name>)")
    zips = []
    try:
        for name in libraries:
            LIBRARY = name
            zips.append(pack())
    finally:
        LIBRARY = libraries if len(libraries) > 1 else libraries[0]
    if len(zips) > 1:
        print("\nZips to share: " + ", ".join(os.path.basename(z) for z in zips if z))
    return zips


if __name__ == "__main__":
    pack_all()
