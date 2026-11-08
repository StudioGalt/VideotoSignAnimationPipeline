import bpy, json, math, os, re
from mathutils import Matrix, Vector

# Build the MetaHuman character file: the MetaHuman's body and face from Unreal, with a control rig
# on the body skeleton. Run once (and again when the MetaHuman changes), in an empty Blender file.
#
# Reads what export_metahuman.py writes from Unreal into MH_EXPORT_FOLDER:
#     <blueprint>_body.fbx    body mesh + skeleton
#     <blueprint>_face.fbx    face mesh + facial joints
# and saves the result as <blueprint>.blend in CHARACTER_BLEND's folder (NAME_FROM_METAHUMAN). import_unreal_take.py is then run from that file: it
# keys each take onto this rig's controls and files the take as its own .blend.
#
# The control rig (one armature, RIG_NAME; the MetaHuman bones stay as they are and deform the mesh):
#   FK controls, named like the Galtis rig so the procedural edits read the same:
#     hip, chest_1..chest_5, neckfk_01, neckfk, headfk, collar_L, bicepfk_L, forearmfk_L, handfk_L,
#     <finger>fk_metacarpal_L, <finger>fk_01_L .. _03_L, thighfk_L, shinfk_L, footfk_L, toefk_L (and _R)
#     Each FK control has the same rest pose as the MetaHuman bone it moves.
#   IK: handik_L, elbow_target_L (arm), footik_L, knee_target_L (leg); chain bones bicepik_L / forearmik_L
#     and thighik_L / shinik_L are moved by the IK and need no keys.
#   "FKIK Switch" bone with ArmIKFK_L/R and LegIKFK_L/R: 0 = FK, 1 = IK (as on Galtis).
#   Fingers and toes follow the hand and foot whether the limb is in FK or IK.
#   master: moves the whole rig.
# Running it again in the character file rebuilds the controls (the meshes aren't imported again).

# --- config ---
# Settings left as None come from pipeline_config.json in the workspace (create_workspace.py makes it).
MH_EXPORT_FOLDER = None   # the export, or the folder above it: then unreal.metahuman_blueprint's folder in it, else the
                          # most recently exported MetaHuman in it. None = paths.metahuman_export (<workspace>/MetaHuman Export)
CHARACTER_BLEND = None    # saved here; "" = don't save. None = paths.character_blend
                          # (<workspace>/MetaHuman/MetaHuman_character.blend)
NAME_FROM_METAHUMAN = True   # True = save as <MetaHuman name>.blend in CHARACTER_BLEND's folder (the name comes from
                             # the export's <name>_body.fbx); False = save to CHARACTER_BLEND exactly as given
BODY_SCALE = 100.0        # same as import_unreal_take.py, so the takes and the character are the same size
RIG_NAME = "MH_rig"       # the body armature, with the controls
FACE_SKELETON_NAME = "MH_face_skeleton"
CLOTHING_SKELETON_NAME = "MH_clothing_skeleton"
PLAIN_MATERIALS = True    # Unreal's MetaHuman materials don't survive FBX (they come in black): give every slot a
                          # plain viewport material instead - skin, eyes, teeth, hair, clothing; eye overlays hidden
FACE_RIG = ""             # another face armature already in this file to attach to the head (the DNA add-on's
                          # head rig, say). "" = any armature with FACIAL_ bones
IMPORT_OPS = ("wm.fbx_import", "import_scene.fbx")   # the same importers, in the same order, as the take import
TWIST = True              # forearm and shin twist bones share the wrist / ankle twist (for the MetaHuman preview)
WIDGET_SCALE = 1.0        # size of the control shapes
IMPORT_DNA = True         # load the head DNA (written by export_metahuman.py) with the MetaHuman DNA add-on, so the face
                          # runs RigLogic and follows the face board; skipped when the file already has a DNA head
DNA_SHAPE_KEYS = True     # also load the face's corrective shape keys (about 800; finer expressions, slower to load)
DNA_IMPORT_OPS = ("character_dna_pro.import_dna", "character_dna.import_dna")   # the add-on's DNA import, Pro first
DNA_ADDON_MODULES = ("character_dna_pro", "character_dna")   # the Character DNA add-on's module names (Pro, standard)
REQUIRE_DNA_ADDON = False # True = stop when the Character DNA add-on isn't enabled; False = warn, and build the rest
MATERIALS_SCRIPT = "apply_plain_materials.py"   # the plain materials (body, outfit, faces), run from next to this script
UNREAL_MATERIALS_SCRIPT = "apply_unreal_materials.py"   # then Unreal's textures (skin, eyes, teeth) and the hair cards,
                                                        # from the export, where export_metahuman.py wrote them
FACE_BOARD_SCRIPT = "fit_face_board.py"   # sizes the DNA add-on's face board and puts it beside the head
LIGHTS_SCRIPT = "add_lights.py"         # sun lights and a grey world for Rendered shading, run from next to this script
PANEL_SCRIPT = "metahuman_rig_panel.py"   # the "Metahuman Rig" sidebar tab, put in the file from next to this script
POSE_LIBRARY = ("Body_Poses_Basic", "Body_Poses_Advanced", "Face_Poses")   # Storage/<this> (the rig's poses: converted ones, or a shared pose pack) - with every other
                          # Storage folder (add_asset_libraries.py, next to this script) - is added to the Asset
                          # Browser's libraries under its name; "" = leave the Preferences alone
LIBRARIES_SCRIPT = "add_asset_libraries.py"

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
SIDES = (("l", "L"), ("r", "R"))


def control_map():
    """MetaHuman bone -> control bone keyed for it."""
    names = {"pelvis": "hip", "spine_01": "chest_1", "spine_02": "chest_2", "spine_03": "chest_3",
             "spine_04": "chest_4", "spine_05": "chest_5", "neck_01": "neckfk_01", "neck_02": "neckfk",
             "head": "headfk"}
    for s, S in SIDES:
        names.update({f"clavicle_{s}": f"collar_{S}", f"upperarm_{s}": f"bicepfk_{S}",
                      f"lowerarm_{s}": f"forearmfk_{S}", f"hand_{s}": f"handfk_{S}",
                      f"thigh_{s}": f"thighfk_{S}", f"calf_{s}": f"shinfk_{S}",
                      f"foot_{s}": f"footfk_{S}", f"ball_{s}": f"toefk_{S}"})
        for f in FINGERS:
            names[f"{f}_metacarpal_{s}"] = f"{f}fk_metacarpal_{S}"
            for n in (1, 2, 3):
                names[f"{f}_0{n}_{s}"] = f"{f}fk_0{n}_{S}"
    return names


def limb_chains():
    """IK chains: (switch property, upper, lower, end, chain bones, IK control, pole, bend sign)."""
    chains = []
    for s, S in SIDES:
        chains.append({"switch": f"ArmIKFK_{S}", "upper": f"upperarm_{s}", "lower": f"lowerarm_{s}", "end": f"hand_{s}",
                       "ik": (f"bicepik_{S}", f"forearmik_{S}"), "control": f"handik_{S}", "pole": f"elbow_target_{S}",
                       "fk": (f"bicepfk_{S}", f"forearmfk_{S}", f"handfk_{S}"), "side": S, "kind": "arm"})
        chains.append({"switch": f"LegIKFK_{S}", "upper": f"thigh_{s}", "lower": f"calf_{s}", "end": f"foot_{s}",
                       "ik": (f"thighik_{S}", f"shinik_{S}"), "control": f"footik_{S}", "pole": f"knee_target_{S}",
                       "fk": (f"thighfk_{S}", f"shinfk_{S}", f"footfk_{S}"), "side": S, "kind": "leg"})
    return chains


SWITCH_BONE = "FKIK Switch"
COLLECTIONS = {"torso": "Torso", "fk": "Limbs FK", "ik": "Limbs IK", "fingers": "Fingers", "switch": "Switch",
               "deform": "Deform", "mch": "MCH"}
TAG = "sgm_control"        # custom property on every bone this script adds, so a rebuild can find them
CON_PREFIX = "SGM "        # name prefix of the constraints it adds


# --- helpers ---
def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one.
    The config is looked for above the open .blend, above this script, and in the workspace create_workspace.py
    noted on this computer; pipeline_config.py is loaded from next to this script or that workspace's Scripts."""
    import json, sys
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
        import pipeline_config
    except ImportError:
        return None, None
    try:
        cfg = pipeline_config.load_config([p for p in [bpy.data.filepath] + folders if p])
    except (OSError, ValueError) as err:
        print(f"  pipeline_config.json couldn't be read: {err}")
        return pipeline_config, None
    return (pipeline_config, cfg) if cfg.get("_config_path") else (pipeline_config, None)


def workspace_path(key, fallback=None):
    """A folder or file from the "paths" of pipeline_config.json (absolute), or `fallback` without settings."""
    pc, cfg = pipeline_settings()
    if cfg is None:
        return fallback
    getter = getattr(pc, "setting_path", None)
    if getter is not None:
        return getter(cfg, key, fallback)
    try:
        return pc.project_path(cfg, key)
    except KeyError:
        return fallback


def dna_addon():
    """The enabled Character DNA add-on's module name (Pro or standard), or None."""
    enabled = [addon.module for addon in bpy.context.preferences.addons]
    for wanted in DNA_ADDON_MODULES:
        for module in enabled:
            if module == wanted or module.endswith("." + wanted):   # an extension is bl_ext.<repository>.<name>
                return module
    for idname in DNA_IMPORT_OPS:                   # installed some other way: its import operator is registered
        mod, op = idname.split(".")
        try:
            getattr(getattr(bpy.ops, mod), op).get_rna_type()
            return mod
        except (AttributeError, KeyError, RuntimeError):
            continue
    return None


def check_dna_addon():
    """Warn (in the console and a popup), or stop with REQUIRE_DNA_ADDON, when the Character DNA add-on is off."""
    if not IMPORT_DNA:
        return
    module = dna_addon()
    if module:
        print(f"  Character DNA add-on: {module}")
        return
    lines = ["The Character DNA add-on (Character DNA Pro, or the standard edition) isn't enabled.",
             "Without it the head DNA isn't loaded: the face is the plain FBX face, which doesn't run RigLogic",
             "and doesn't follow the face board, so face board animation won't move it.",
             "Install / enable it in Edit > Preferences > Add-ons (or Get Extensions), then run this script again."]
    if REQUIRE_DNA_ADDON:
        lines.append("REQUIRE_DNA_ADDON is True, so nothing was built.")
    else:
        lines.append("The body rig, textures and hair are built anyway (REQUIRE_DNA_ADDON = True stops here instead).")
    print("\n  " + "!" * 100)
    for line in lines:
        print(f"  WARNING: {line}")
    print("  " + "!" * 100 + "\n")

    def draw(self, context):
        for line in lines:
            self.layout.label(text=line)
    try:
        bpy.context.window_manager.popup_menu(draw, title="build_metahuman_rig: Character DNA add-on missing",
                                              icon="ERROR")
    except Exception:
        pass
    if REQUIRE_DNA_ADDON:
        raise RuntimeError("The Character DNA add-on isn't enabled (REQUIRE_DNA_ADDON = True): enable it and run again")


def resolve_settings():
    """Fill MH_EXPORT_FOLDER and CHARACTER_BLEND from pipeline_config.json where they are None."""
    global MH_EXPORT_FOLDER, CHARACTER_BLEND
    pc, cfg = pipeline_settings()
    print(f"  settings: {cfg['_config_path']}" if cfg else "  settings: no pipeline_config.json found (run create_workspace.py)")
    if not MH_EXPORT_FOLDER:
        MH_EXPORT_FOLDER = workspace_path("metahuman_export")
        blueprint = pc.get(cfg, "unreal.metahuman_blueprint", "") if cfg else ""
        name = blueprint.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0] if blueprint else ""
        if MH_EXPORT_FOLDER and name and os.path.isdir(os.path.join(MH_EXPORT_FOLDER, name)):
            MH_EXPORT_FOLDER = os.path.join(MH_EXPORT_FOLDER, name)
    if not MH_EXPORT_FOLDER:
        raise RuntimeError("No pipeline_config.json found, so the MetaHuman export's folder isn't known: run "
                           "create_workspace.py, or set MH_EXPORT_FOLDER at the top of this script")
    if CHARACTER_BLEND is None:
        CHARACTER_BLEND = workspace_path("character_blend", "")
    print(f"  MetaHuman export: {MH_EXPORT_FOLDER}")


def view3d_context():
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else []):
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    return {"window": window, "area": area, "region": region}
    return {}


def script_folders():
    """Where this script was opened from (Text Editor), to find the scripts that sit next to it."""
    folders = []
    here = globals().get("__file__")
    if here and os.path.isfile(here):
        folders.append(os.path.dirname(os.path.abspath(here)))
    for text in bpy.data.texts:
        if text.filepath and os.path.basename(text.filepath) == "build_metahuman_rig.py":
            folders.append(os.path.dirname(bpy.path.abspath(text.filepath)))
    return folders


def object_mode():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def import_fbx(path):
    """Import an FBX with the first importer that runs. Returns the new objects."""
    before = set(bpy.data.objects)
    for idname in IMPORT_OPS:
        mod, op = idname.split(".")
        fn = getattr(getattr(bpy.ops, mod, None), op, None)
        if fn is None:
            continue
        try:
            with bpy.context.temp_override(**view3d_context()):
                if not fn.poll():
                    continue
                try:
                    fn("EXEC_DEFAULT", filepath=path, global_scale=BODY_SCALE)
                except TypeError:
                    fn("EXEC_DEFAULT", filepath=path)
                    print(f"  {idname} has no scale option: imported at its own size")
        except RuntimeError as e:
            print(f"  {idname}: {e}")
            continue
        new = [o for o in bpy.data.objects if o not in before]
        if new:
            print(f"  imported {os.path.basename(path)} with {idname}: {len(new)} objects")
            return new
    raise RuntimeError(f"couldn't import {path}")


def export_folder(folder):
    """The folder holding the *_body.fbx: `folder` itself, else its most recently exported sub-folder."""
    if not os.path.isdir(folder):
        return folder
    if any(f.lower().endswith("_body.fbx") for f in os.listdir(folder)):
        return folder
    subs = []
    for name in os.listdir(folder):
        sub = os.path.join(folder, name)
        if os.path.isdir(sub):
            bodies = [os.path.join(sub, f) for f in os.listdir(sub) if f.lower().endswith("_body.fbx")]
            if bodies:
                subs.append((max(os.path.getmtime(b) for b in bodies), sub))
    if not subs:
        return folder
    subs.sort()
    if len(subs) > 1:
        print(f"  {len(subs)} MetaHumans exported; using the newest, {os.path.basename(subs[-1][1])} "
              f"(others: {', '.join(os.path.basename(s) for _, s in subs[:-1])})")
    return subs[-1][1]


def find_export(folder, part):
    if not os.path.isdir(folder):
        return None
    hits = sorted(f for f in os.listdir(folder) if f.lower().endswith(f"_{part}.fbx"))
    return os.path.join(folder, hits[0]) if hits else None


def metahuman_name(body_fbx, folder):
    """The MetaHuman's name: <name> of <name>_body.fbx, else the export folder's name. Safe as a file name."""
    name = ""
    if body_fbx:
        name = re.sub(r"_body\.fbx$", "", os.path.basename(body_fbx), flags=re.I)
    if not name and folder:
        name = os.path.basename(os.path.normpath(folder))
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")


def find_exports(folder, part):
    """Every *_<part>.fbx and *_<part>_<n>.fbx in the folder."""
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if re.search(rf"_{part}(_\d+)?\.fbx$", f, re.I)]


def find_dna(folder):
    """The head .dna in the export folder (or a folder inside it); None when there is none."""
    found = []
    for root, _, files in os.walk(folder) if os.path.isdir(folder) else []:
        for f in files:
            if f.lower().endswith(".dna") and "body" not in f.lower():
                path = os.path.join(root, f)
                found.append((0 if "head" in f.lower() else 1, -os.path.getmtime(path), path))
    return sorted(found)[0][2] if found else None


def import_dna(path):
    """Load a head DNA with the MetaHuman DNA add-on. Returns True when it ran."""
    if round(bpy.context.scene.unit_settings.scale_length, 2) != 1.0:
        print(f"  head DNA: the add-on needs the scene's Unit Scale at 1.0 (it is "
              f"{bpy.context.scene.unit_settings.scale_length:g}); set it and run again")
        return False
    for idname in DNA_IMPORT_OPS:
        mod, op = idname.split(".")
        fn = getattr(getattr(bpy.ops, mod, None), op, None)
        if fn is None:
            continue
        try:
            with bpy.context.temp_override(**view3d_context()):
                if not fn.poll():
                    print(f"  head DNA: {idname} can't run (the add-on's dependencies aren't installed?)")
                    continue
                result = None
                for options in ({"include_body": False, "import_shape_keys": DNA_SHAPE_KEYS}, {"include_body": False}, {}):
                    try:
                        result = fn("EXEC_DEFAULT", filepath=path, **options)
                        break
                    except TypeError:                # this edition doesn't have one of the options
                        continue
        except AttributeError:                       # not registered: the add-on (or this edition of it) isn't on
            continue
        except RuntimeError as e:
            print(f"  head DNA: {idname} failed: {e}")
            continue
        if "FINISHED" in result:
            print(f"  head DNA: loaded {os.path.basename(path)} with {idname}")
            return True
    print("  head DNA: the MetaHuman DNA add-on isn't enabled (or failed) - the face stays the FBX face, which "
          f"doesn't follow the face board. Enable the add-on and run again, or import {path} with it by hand.")
    return False


def is_face_armature(obj):
    return obj.type == "ARMATURE" and any(b.name.startswith("FACIAL_") for b in obj.data.bones)


# --- widgets ---
def widget(name, verts, edges):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        return obj
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    obj = bpy.data.objects.new(name, mesh)
    coll = bpy.data.collections.get("MH_widgets") or bpy.data.collections.new("MH_widgets")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    coll.objects.link(obj)
    coll.hide_viewport = coll.hide_render = True
    return obj


def circle(n=24, r=1.0, plane="XZ", y=0.0):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        c, s = r * math.cos(a), r * math.sin(a)
        pts.append((c, y, s) if plane == "XZ" else (c, s, y) if plane == "XY" else (y, c, s))
    return pts, [(i, (i + 1) % n) for i in range(n)]


def widgets():
    v, e = circle(r=0.5)
    ring = widget("WGT_ring", v, e)
    v, e = circle(r=1.0, y=0.0)
    big = widget("WGT_ring_big", v, e)
    cube_v = [(x, y, z) for x in (-.5, .5) for y in (0, 1) for z in (-.5, .5)]
    cube_e = [(a, b) for a in range(8) for b in range(a + 1, 8)
              if sum(1 for i in range(3) if cube_v[a][i] != cube_v[b][i]) == 1]
    box = widget("WGT_box", cube_v, cube_e)
    sv, se = [], []
    for plane in ("XZ", "XY", "YZ"):
        pv, pe = circle(n=16, r=0.5, plane=plane)
        se += [(a + len(sv), b + len(sv)) for a, b in pe]
        sv += pv
    ball = widget("WGT_ball", sv, se)
    dv = [(0, 0, 0), (0.4, 0.5, 0), (0, 1, 0), (-0.4, 0.5, 0), (0, 0.5, 0.4), (0, 0.5, -0.4)]
    de = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 2), (2, 5), (5, 0), (1, 4), (4, 3), (3, 5), (5, 1)]
    diamond = widget("WGT_diamond", dv, de)
    return {"ring": ring, "big": big, "box": box, "ball": ball, "diamond": diamond}


# --- the face ---
def world_rest(obj, name):
    return obj.matrix_world @ obj.data.bones[name].matrix_local


def align_follower(body, other):
    """
    Move and scale another skeleton (a face from the DNA add-on, say) so its head sits on the body's head, at the
    body's size. Skeletons imported the same way as the body are already in place and aren't touched.
    """
    pair = next(((a, b) for a, b in (("neck_01", "head"), ("spine_05", "head"), ("pelvis", "head"))
                 if all(n in body.data.bones and n in other.data.bones for n in (a, b))), None)
    if pair is None:
        return
    a, b = pair
    hb, ab = body.matrix_world @ body.data.bones[b].head_local, body.matrix_world @ body.data.bones[a].head_local
    hf, af = other.matrix_world @ other.data.bones[b].head_local, other.matrix_world @ other.data.bones[a].head_local
    if (hf - af).length < 1e-9:
        return
    ratio = (hb - ab).length / (hf - af).length
    if abs(ratio - 1.0) < 1e-3 and (hb - hf).length < 1e-3 * (hb - ab).length:
        return
    move = Matrix.Translation(hb) @ Matrix.Scale(ratio, 4) @ Matrix.Translation(-hf)
    def under(obj):
        while obj is not None:
            if obj is other:
                return True
            obj = obj.parent
        return False
    movers = [other] + [m for m in meshes_of([other]) if not under(m.parent)]
    for obj in movers:
        obj.matrix_world = move @ obj.matrix_world
    bpy.context.view_layer.update()
    turn = math.degrees((hb - ab).angle((hf - af)))
    print(f"  '{other.name}' moved onto the body's head and scaled x{ratio:.4g}"
          + (f" ({len(movers) - 1} mesh(es) moved with it)" if len(movers) > 1 else "")
          + (f" - WARNING: its neck leans {turn:.0f} degrees differently from the body's" if turn > 10 else ""))


def follow_plan(body, other):
    """
    For each bone the other skeleton shares with the body: (name, None) when its rest pose is the body bone's
    (same axes, place and size: it can copy the body bone), else (name, its rest pose in the body's armature
    space), for a helper bone on the body that carries that rest pose along.
    """
    plan = []
    to_body = body.matrix_world.inverted()
    for bone in other.data.bones:
        if bone.name not in body.data.bones:
            continue
        mb, mf = world_rest(body, bone.name), world_rest(other, bone.name)
        qb, qf = mb.to_3x3().normalized().to_quaternion(), mf.to_3x3().normalized().to_quaternion()
        sb, sf = mb.to_scale(), mf.to_scale()
        size = max(body.data.bones[bone.name].length * sb[1], 1e-6)
        same = (math.degrees(qb.rotation_difference(qf).angle) < 0.5
                and all(abs(x - y) <= 0.01 * max(abs(x), 1e-9) for x, y in zip(sb, sf))
                and (mb.translation - mf.translation).length < 0.01 * size)
        plan.append((bone.name, None if same else (to_body @ mf, bone.length * sf[1] / max(body.matrix_world.to_scale()[1], 1e-9))))
    return plan


def attach_face(body, face, plan, index):
    """Make the other skeleton's shared bones (root .. head for a face, most of them for an outfit) follow the body."""
    for name, helper in plan:
        pb = face.pose.bones[name]
        for c in [c for c in pb.constraints if c.name.startswith(CON_PREFIX)]:
            pb.constraints.remove(c)
        if helper is None:
            c = pb.constraints.new("COPY_TRANSFORMS")
            c.name = CON_PREFIX + "follow body"
            c.target, c.subtarget = body, name
        else:
            for kind in ("COPY_LOCATION", "COPY_ROTATION"):
                c = pb.constraints.new(kind)
                c.name = CON_PREFIX + "follow body " + kind.split("_")[1].lower()
                c.target, c.subtarget = body, follow_name(name, index)
    helpers = sum(1 for _, h in plan if h is not None)
    print(f"  '{face.name}': {len(plan)} bones follow the body"
          + (f" ({helpers} through helper bones: different axes or size)" if helpers else "")
          + ("" if plan else " - NONE share a name with the body skeleton, so it isn't attached"))
    return len(plan)


def follow_name(name, index):
    return f"{name}_follow{index}"


# --- materials ---
PLAIN = [  # (words in the material or object name, name, colour RGBA, hidden)
    (("occlusion", "lacrimal", "tear", "cartilage", "eyeshell", "eye_shell", "eyeedge", "eye_edge"), "MH_plain_hidden", (0.0, 0.0, 0.0, 0.0), True),
    (("eyelash", "lash", "brow", "hair", "beard", "mustache", "moustache", "peachfuzz", "fuzz"), "MH_plain_hair", (0.12, 0.08, 0.05, 1.0), False),
    (("eye",), "MH_plain_eye", (0.85, 0.85, 0.82, 1.0), False),
    (("teeth", "tooth"), "MH_plain_teeth", (0.9, 0.88, 0.8, 1.0), False),
    (("tongue", "mouth", "gum"), "MH_plain_mouth", (0.6, 0.3, 0.3, 1.0), False),
    (("cloth", "outfit", "shirt", "pant", "top", "shoe", "torso", "legs", "feet", "garment"), "MH_plain_clothing", (0.25, 0.3, 0.38, 1.0), False),
    ((), "MH_plain_skin", (0.78, 0.58, 0.48, 1.0), False),
]


def plain_material(name, color, hidden):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if mat.node_tree is None:                     # (nodes are always on from Blender 5)
            mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
            bsdf.inputs["Alpha"].default_value = color[3]
        mat.diffuse_color = color if not hidden else (0.0, 0.0, 0.0, 0.0)
        if hidden:
            for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
                try:
                    setattr(mat, attr, value)
                except (AttributeError, TypeError):
                    pass
    return mat


def plain_materials(meshes):
    """Replace each slot's material by a plain one chosen from the material's (or mesh's) name."""
    counts = {}
    for obj in meshes:
        for slot in obj.material_slots:
            text = (slot.material.name if slot.material else obj.name).lower()
            if slot.material and slot.material.name.startswith(("MH_plain_", "MH_tex_", "MH_hair_")):
                continue                                  # done on an earlier run
            for words, name, color, hidden in PLAIN:
                if not words or any(w in text for w in words):
                    break
            if name == "MH_plain_skin" and any(w in obj.name.lower() for w in ("cloth", "outfit")):
                name, color, hidden = "MH_plain_clothing", PLAIN[-2][2], False
            slot.material = plain_material(name, color, hidden)
            counts[name] = counts.get(name, 0) + 1
    if counts:
        print("  plain materials: " + ", ".join(f"{n[9:]} x{c}" for n, c in sorted(counts.items())))


def meshes_of(armatures):
    found = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        mod_targets = {m.object for m in obj.modifiers if m.type == "ARMATURE"}
        if obj.parent in armatures or mod_targets & set(armatures):
            found.append(obj)
    return found


# --- the rig ---
def clear_old(rig):
    """Remove what an earlier run added: tagged bones, and SGM constraints and drivers on the MetaHuman bones."""
    object_mode()
    bpy.context.view_layer.objects.active = rig
    if rig.animation_data:
        for fc in list(rig.animation_data.drivers):
            if f'constraints["{CON_PREFIX}' in fc.data_path:
                rig.animation_data.drivers.remove(fc)
    for obj in [o for o in bpy.data.objects if o.type == "ARMATURE"]:     # the rig, and skeletons that follow it
        for pb in obj.pose.bones:
            for c in [c for c in pb.constraints if c.name.startswith(CON_PREFIX)]:
                pb.constraints.remove(c)
    tagged = [b.name for b in rig.data.bones if b.get(TAG)]
    if tagged:
        bpy.ops.object.mode_set(mode="EDIT")
        for name in tagged:
            rig.data.edit_bones.remove(rig.data.edit_bones[name])
        bpy.ops.object.mode_set(mode="OBJECT")
        print(f"  removed {len(tagged)} control bones of an earlier build")


def directions(rig):
    """(up, forward, size) in the armature's own space, from where the bones are."""
    to_arm = rig.matrix_world.inverted().to_3x3()
    up = (to_arm @ Vector((0, 0, 1))).normalized()
    bones = rig.data.bones
    right = None
    for left, rgt in (("upperarm_l", "upperarm_r"), ("thigh_l", "thigh_r")):
        if left in bones and rgt in bones:
            right = bones[rgt].head_local - bones[left].head_local
            break
    if right is None or right.length < 1e-9:
        forward = (to_arm @ Vector((0, -1, 0))).normalized()
    else:
        forward = up.cross(right.normalized()).normalized()      # the character's left is +X when it faces -Y
    top = bones["head"].head_local if "head" in bones else max((b.head_local for b in bones), key=lambda v: v.dot(up))
    base = bones["root"].head_local if "root" in bones else min((b.head_local for b in bones), key=lambda v: v.dot(up))
    size = max((top - base).dot(up), 1e-3)
    return up, forward, size


def signed_angle(u, v, normal):
    a = u.angle(v)
    return -a if u.cross(v).dot(normal) < 0 else a


def pole_angle(base, tip_tail, pole):
    """IK pole angle that keeps the chain as it is in the rest pose."""
    axis = base.tail - base.head
    normal = (tip_tail - base.head).cross(pole - base.head)
    projected = normal.cross(axis)
    return signed_angle(base.x_axis, projected, axis)


def build_rig(rig, plans=()):
    cmap = {d: c for d, c in control_map().items() if d in rig.data.bones}
    missing = [d for d in control_map() if d not in rig.data.bones]
    chains = [ch for ch in limb_chains() if all(n in rig.data.bones for n in (ch["upper"], ch["lower"], ch["end"]))]
    up, forward, size = directions(rig)
    print(f"  {len(cmap)} MetaHuman bones get an FK control; {len(chains)} IK limbs"
          + (f"; not in this skeleton: {', '.join(missing[:8])}" + (" ..." if len(missing) > 8 else "") if missing else ""))
    limb_ends = {ch["end"] for ch in chains}
    twists = []

    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    original = [b.name for b in eb]

    def new(name, head, tail, parent=None, roll=0.0, connect=False):
        b = eb.new(name)
        b.head, b.tail, b.roll = head, tail, roll
        b.parent, b.use_connect, b.use_deform = parent, connect, False
        return b

    def dup(src, name, parent):
        return new(name, src.head.copy(), src.tail.copy(), parent, src.roll)

    root_head = eb["root"].head.copy() if "root" in eb else Vector()
    master = new("master", root_head, root_head + forward * size * 0.25, None)

    def depth(name):
        b, n = eb[name], 0
        while b.parent:
            b, n = b.parent, n + 1
        return n

    def control_parent(name):
        """The control of the nearest controlled bone above; a hand or foot itself for fingers and toes."""
        above = eb[name].parent
        while above is not None:
            if above.name in limb_ends:
                return above                     # follows the hand / foot in FK and IK alike
            if above.name in cmap:
                return eb[cmap[above.name]]
            above = above.parent
        return master

    for name in sorted(cmap, key=depth):                       # parents first, so their controls exist
        dup(eb[name], cmap[name], control_parent(name))

    for ch in chains:
        upper, lower, end = eb[ch["upper"]], eb[ch["lower"]], eb[ch["end"]]
        a, b, c = upper.head.copy(), lower.head.copy(), end.head.copy()
        line = c - a
        off = b - (a + line * ((b - a).dot(line) / max(line.length_squared, 1e-12)))
        if off.length > 1e-4 * size:
            bend = off.normalized()
        else:
            bend = -forward if ch["kind"] == "arm" else forward
        first = new(ch["ik"][0], a, b, upper.parent)
        second = new(ch["ik"][1], b, c, first, connect=True)
        # the chain's roll: X axis across the bend, so the IK bends in its own plane
        for bone in (first, second):
            bone.align_roll(bone.vector.cross(bend))
        side = ch["side"]
        dup(upper, f"{ch['upper']}_ikmch", first)
        dup(lower, f"{ch['lower']}_ikmch", second)
        dup(end, f"{ch['end']}_ikmch", second)
        dup(end, ch["control"], master)
        reach = max((b - a).length, 1e-3)
        pole_at = b + bend * reach
        new(ch["pole"], pole_at, pole_at + up * reach * 0.25, master)
        ch["pole_angle"] = pole_angle(first, second.tail, pole_at)

    switch_at = root_head + up * size * 1.1
    new(SWITCH_BONE, switch_at, switch_at + up * size * 0.08, master)

    if TWIST:
        for s, _ in SIDES:
            for twist_parent, joint_a, joint_b, follow in (
                    (f"lowerarm_{s}", f"lowerarm_{s}", f"hand_{s}", f"hand_{s}"),
                    (f"calf_{s}", f"calf_{s}", f"foot_{s}", f"foot_{s}")):
                if not all(n in eb for n in (joint_a, joint_b, follow)):
                    continue
                a, b = eb[joint_a].head.copy(), eb[joint_b].head.copy()
                axis = b - a
                for bone in [x for x in eb if x.parent and x.parent.name == twist_parent and "twist" in x.name
                             and x.name in original]:
                    along = max(0.0, min(1.0, (bone.head - a).dot(axis) / max(axis.length_squared, 1e-12)))
                    local = bone.matrix.to_3x3()
                    axes = [local.col[i].normalized() for i in range(3)]
                    long_i = max(range(3), key=lambda i: abs(axes[i].dot(axis.normalized())))
                    track_i = (long_i + 1) % 3
                    aim = new(f"{bone.name}_aim", b + axes[track_i] * axis.length * 0.3,
                              b + axes[track_i] * axis.length * 0.3 + axes[long_i] * axis.length * 0.05, eb[follow])
                    twists.append((bone.name, aim.name, long_i, track_i, along))

    for index, plan in enumerate(plans):
        for name, helper in plan:
            if helper is None:
                continue
            matrix, length = helper
            b = eb.new(follow_name(name, index))
            b.head, b.tail = Vector((0, 0, 0)), Vector((0, max(length, 1e-4), 0))
            m = matrix.to_3x3().normalized().to_4x4()
            m.translation = matrix.translation
            b.matrix = m
            b.length = max(length, 1e-4)
            b.parent, b.use_deform = eb[name], False

    added = [b.name for b in eb if b.name not in original]
    bpy.ops.object.mode_set(mode="OBJECT")
    for name in added:
        rig.data.bones[name][TAG] = True
    return cmap, chains, twists, size


def add_constraints(rig, cmap, chains, twists):
    pose = rig.pose.bones
    sw = pose[SWITCH_BONE]
    for ch in chains:
        sw[ch["switch"]] = 0.0
        ui = sw.id_properties_ui(ch["switch"])
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=f"{ch['kind']} {ch['side']}: 0 = FK, 1 = IK")

    def copy(owner, sub, label, kind="COPY_TRANSFORMS"):
        c = pose[owner].constraints.new(kind)
        c.name = CON_PREFIX + label
        c.target, c.subtarget = rig, sub
        return c

    for deform, control in cmap.items():
        copy(deform, control, "FK")
    for ch in chains:
        for part in ("upper", "lower", "end"):
            c = copy(ch[part], f"{ch[part]}_ikmch", "IK")
            fc = c.driver_add("influence")
            fc.driver.type = "AVERAGE"
            var = fc.driver.variables.new()
            var.name, var.type = "ik", "SINGLE_PROP"
            var.targets[0].id_type, var.targets[0].id = "OBJECT", rig
            var.targets[0].data_path = f'pose.bones["{SWITCH_BONE}"]["{ch["switch"]}"]'
        copy(f"{ch['end']}_ikmch", ch["control"], "IK rotation", "COPY_ROTATION")
        ik = pose[ch["ik"][1]].constraints.new("IK")
        ik.name = CON_PREFIX + "IK"
        ik.target, ik.subtarget = rig, ch["control"]
        ik.pole_target, ik.pole_subtarget = rig, ch["pole"]
        ik.pole_angle = ch["pole_angle"]
        ik.chain_count = 2
        ik.use_stretch = False
    for bone, aim, long_i, track_i, along in twists:
        c = pose[bone].constraints.new("LOCKED_TRACK")
        c.name = CON_PREFIX + "twist"
        c.target, c.subtarget = rig, aim
        c.lock_axis = ("LOCK_X", "LOCK_Y", "LOCK_Z")[long_i]
        c.track_axis = ("TRACK_X", "TRACK_Y", "TRACK_Z")[track_i]
        c.influence = along


def dress(rig, cmap, chains, size):
    """Bone collections, shapes, colours and rotation modes."""
    arm = rig.data
    colls = {}
    for key, label in COLLECTIONS.items():
        colls[key] = arm.collections.get(label) or arm.collections.new(label)
    shapes = widgets()
    controls = set(cmap.values())
    ik_controls = {ch["control"] for ch in chains} | {ch["pole"] for ch in chains}
    chain_bones = {n for ch in chains for n in ch["ik"]}
    mch = {b.name for b in arm.bones if b.get(TAG) and (b.name.endswith("_ikmch") or b.name.endswith("_aim")
                                                       or re.search(r"_follow\d+$", b.name))}

    def side_palette(name, center, left, right):
        return left if name.endswith("_L") else right if name.endswith("_R") else center

    for bone in arm.bones:
        for c in list(bone.collections):
            c.unassign(bone)
        pb = rig.pose.bones[bone.name]
        name = bone.name
        if not bone.get(TAG):
            colls["deform"].assign(bone)
            continue
        pb.rotation_mode = "QUATERNION"
        if name in mch or name in chain_bones:
            colls["mch" if name in mch else "ik"].assign(bone)
            if name in chain_bones:
                pb.custom_shape = shapes["ring"]
                bone.color.palette = "THEME03"
            continue
        if name == SWITCH_BONE:
            colls["switch"].assign(bone)
            pb.custom_shape = shapes["diamond"]
            bone.color.palette = "THEME09"
        elif name == "master":
            colls["torso"].assign(bone)
            pb.custom_shape, pb.custom_shape_scale_xyz = shapes["big"], (3.0 * WIDGET_SCALE,) * 3
            bone.color.palette = "THEME09"
        elif name in ik_controls:
            colls["ik"].assign(bone)
            pb.custom_shape = shapes["ball"] if name.startswith(("elbow", "knee")) else shapes["box"]
            bone.color.palette = side_palette(name, "THEME09", "THEME01", "THEME04")
        elif name in controls:
            finger = any(name.startswith(f) for f in FINGERS)
            torso = not name.endswith(("_L", "_R"))
            colls["fingers" if finger else "torso" if torso else "fk"].assign(bone)
            pb.custom_shape = shapes["ring"]
            if torso:
                pb.custom_shape_scale_xyz = (1.6 * WIDGET_SCALE,) * 3
            elif finger:
                pb.custom_shape_scale_xyz = (0.8 * WIDGET_SCALE,) * 3
            else:
                pb.custom_shape_scale_xyz = (WIDGET_SCALE,) * 3
            bone.color.palette = side_palette(name, "THEME09", "THEME01", "THEME04")
    colls["deform"].is_visible = False
    colls["mch"].is_visible = False
    arm.display_type = "OCTAHEDRAL"
    rig.show_in_front = True


def check_rest(rig, cmap):
    """With every control at rest, the MetaHuman bones must be at rest too, in FK and in IK."""
    bpy.context.view_layer.update()
    pose, sw = rig.pose.bones, rig.pose.bones[SWITCH_BONE]
    worst = []
    for value in (0.0, 1.0):
        for key in [k for k in sw.keys() if k.endswith(("IKFK_L", "IKFK_R"))]:
            sw[key] = value
        rig.update_tag()
        bpy.context.view_layer.update()
        worst.append(max((_angle(pose[d].matrix, rig.data.bones[d].matrix_local), d) for d in cmap))
    for key in [k for k in sw.keys() if k.endswith(("IKFK_L", "IKFK_R"))]:
        sw[key] = 0.0
    rig.update_tag()
    bpy.context.view_layer.update()
    return worst


def _angle(a, b):
    qa = a.to_3x3().normalized().to_quaternion()
    qb = b.to_3x3().normalized().to_quaternion()
    return math.degrees(qa.rotation_difference(qb).angle)


# --- run ---
print("[build_metahuman_rig]")
resolve_settings()
check_dna_addon()
object_mode()
rig = bpy.data.objects.get(RIG_NAME)
if rig is None:
    folder = export_folder(MH_EXPORT_FOLDER)
    body_fbx, face_fbx = find_export(folder, "body"), find_export(folder, "face")
    if body_fbx is None:
        raise RuntimeError(f"No *_body.fbx in {MH_EXPORT_FOLDER} or the folders in it. Run export_metahuman.py in Unreal first, "
                           "or set MH_EXPORT_FOLDER to where it wrote the files.")
    new = import_fbx(body_fbx)
    bodies = [o for o in new if o.type == "ARMATURE" and "pelvis" in o.data.bones]
    if not bodies:
        raise RuntimeError(f"{os.path.basename(body_fbx)} has no skeleton with a 'pelvis' bone")
    rig = bodies[0]
    rig.name = rig.data.name = RIG_NAME
    rig["sgm_metahuman"] = metahuman_name(body_fbx, folder)
    print(f"  body: '{rig.name}', {len(rig.data.bones)} bones, meshes: "
          + ", ".join(o.name for o in new if o.type == "MESH"))
else:
    print(f"  '{RIG_NAME}' is already in this file: rebuilding its controls")
    clear_old(rig)
    folder = export_folder(MH_EXPORT_FOLDER)
    face_fbx = find_export(folder, "face")
    if not rig.get("sgm_metahuman"):              # a file built before the name was kept on the rig
        rig["sgm_metahuman"] = metahuman_name(find_export(folder, "body"), folder)
print(f"  MetaHuman: {rig.get('sgm_metahuman') or '(name unknown)'}")


def add_skeleton(path, name):
    """Import a face or outfit FBX and mark its skeleton to follow the body."""
    new = import_fbx(path)
    arms = [o for o in new if o.type == "ARMATURE"]
    for i, arm in enumerate(arms):
        arm.name = arm.data.name = name if i == 0 else f"{name}.{i:03d}"
        arm["sgm_follows_body"] = True
        arm.hide_set(True)
    print(f"  {os.path.basename(path)}: meshes " + ", ".join(o.name for o in new if o.type == "MESH"))


if face_fbx and not any(o.type == "ARMATURE" and (o.name.startswith(FACE_SKELETON_NAME) or is_face_armature(o))
                        for o in bpy.data.objects):
    add_skeleton(face_fbx, FACE_SKELETON_NAME)
elif not face_fbx:
    print(f"  face: no *_face.fbx in {folder}; the body is built without a face")
clothing = find_exports(folder, "clothing")
if clothing and not any(o.name.startswith(CLOTHING_SKELETON_NAME) for o in bpy.data.objects):
    for i, path in enumerate(clothing):
        add_skeleton(path, CLOTHING_SKELETON_NAME if i == 0 else f"{CLOTHING_SKELETON_NAME}_{i + 1}")

has_dna_head = any(o.type == "ARMATURE" and is_face_armature(o) and not o.name.startswith(FACE_SKELETON_NAME)
                   for o in bpy.data.objects)
if IMPORT_DNA and not has_dna_head:
    dna_file = find_dna(folder)
    if dna_file:
        import_dna(dna_file)
    else:
        print(f"  head DNA: no .dna in {folder} (export_metahuman.py writes it with EXPORT_DNA); the face stays the FBX face")

followers = []
for obj in bpy.data.objects:
    if obj is rig or obj.type != "ARMATURE":
        continue
    if obj.get("sgm_follows_body") or (FACE_RIG and obj.name == FACE_RIG) or (not FACE_RIG and is_face_armature(obj)):
        followers.append(obj)
# a face from the DNA add-on (it runs RigLogic, so it moves with the face board) replaces the FBX face
dna_heads = [o for o in followers if is_face_armature(o) and not o.name.startswith(FACE_SKELETON_NAME)]
fbx_faces = [o for o in followers if o.name.startswith(FACE_SKELETON_NAME)]
if dna_heads and fbx_faces:
    for skeleton in fbx_faces:
        for obj in [skeleton] + meshes_of([skeleton]):
            obj.hide_viewport = obj.hide_render = True
            obj.hide_set(True)
    print(f"  face: using '{dna_heads[0].name}' (DNA add-on); the FBX face ({fbx_faces[0].name}) is hidden")
for obj in followers:
    align_follower(rig, obj)
board_script = next((os.path.join(f, FACE_BOARD_SCRIPT) for f in script_folders()
                     if os.path.isfile(os.path.join(f, FACE_BOARD_SCRIPT))), None)
if board_script:
    _scope = {"__name__": "build_metahuman_rig_face_board"}
    with open(board_script, "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), FACE_BOARD_SCRIPT, "exec"), _scope)
    _scope["fit_face_board"](rig)
plans = [follow_plan(rig, obj) for obj in followers]
# Unreal's textures first (while the slots still carry Unreal's material names), then plain materials for the rest
unreal_materials = next((os.path.join(f, UNREAL_MATERIALS_SCRIPT) for f in script_folders()
                         if os.path.isfile(os.path.join(f, UNREAL_MATERIALS_SCRIPT))), None)
if unreal_materials:
    _scope = {"__name__": "build_metahuman_rig_unreal_materials"}
    with open(unreal_materials, "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), UNREAL_MATERIALS_SCRIPT, "exec"), _scope)
    _scope.update(MH_EXPORT_FOLDER=folder, RIG_NAME=RIG_NAME, FACE_SKELETON_NAME=FACE_SKELETON_NAME,
                  CLOTHING_SKELETON_NAME=CLOTHING_SKELETON_NAME, BODY_SCALE=BODY_SCALE)
    _scope["apply_unreal_materials"]()
else:
    print(f"  {UNREAL_MATERIALS_SCRIPT} isn't next to this script: plain materials only, no hair")
if PLAIN_MATERIALS:
    materials_script = next((os.path.join(f, MATERIALS_SCRIPT) for f in script_folders()
                             if os.path.isfile(os.path.join(f, MATERIALS_SCRIPT))), None)
    if materials_script:
        _scope = {"__name__": "build_metahuman_rig_materials"}
        with open(materials_script, "r", encoding="utf-8") as fh:
            exec(compile(fh.read(), MATERIALS_SCRIPT, "exec"), _scope)
        _scope["apply_plain_materials"](meshes_of([rig] + followers))
    else:
        plain_materials([m for m in meshes_of([rig] + followers) if not any(m in meshes_of([d]) for d in dna_heads)])

cmap, chains, twists, size = build_rig(rig, plans)
for index, (obj, plan) in enumerate(zip(followers, plans)):
    attach_face(rig, obj, plan, index)
add_constraints(rig, cmap, chains, twists)
dress(rig, cmap, chains, size)
rig["sgm_rig"] = "metahuman"
rig["sgm_control_map"] = json.dumps(cmap)
rig["sgm_limbs"] = json.dumps([{k: v for k, v in ch.items() if k != "pole_angle"} for ch in chains])
fk_off, ik_off = check_rest(rig, cmap)
print(f"  rest check: MetaHuman bones off their rest pose by {fk_off[0]:.3f} deg in FK ({fk_off[1]}), "
      f"{ik_off[0]:.3f} deg in IK ({ik_off[1]})")
if max(fk_off[0], ik_off[0]) > 1.0:
    print("  WARNING: the rig moves the skeleton out of its rest pose; tell me the bone named above")
print(f"  controls: {len(cmap)} FK, {len(chains)} IK limbs ({', '.join(ch['control'] for ch in chains)}), "
      f"{len(twists)} twist bones, switch '{SWITCH_BONE}'")

def add_panel():
    """Put metahuman_rig_panel.py (next to this script) in the file as a registered text, and run it."""
    folders = script_folders()
    path = next((os.path.join(f, PANEL_SCRIPT) for f in folders if os.path.isfile(os.path.join(f, PANEL_SCRIPT))), None)
    if path is None:
        print(f"  {PANEL_SCRIPT} isn't next to this script: no 'Metahuman Rig' tab (put it there and run again)")
        return
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    text = bpy.data.texts.get(PANEL_SCRIPT) or bpy.data.texts.new(PANEL_SCRIPT)
    text.clear()
    text.write(source)
    text.use_module = True
    exec(compile(source, PANEL_SCRIPT, "exec"), {"__name__": "metahuman_rig_panel"})
    print("  'Metahuman Rig' tab added to the 3D Viewport sidebar (N); it is saved in this file")


def add_lights():
    """Run add_lights.py (next to this script): sun lights and a grey world, for Rendered shading."""
    for folder in script_folders():
        path = os.path.join(folder, LIGHTS_SCRIPT)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                exec(compile(fh.read(), LIGHTS_SCRIPT, "exec"), {"__name__": "add_lights"})
            return
    print(f"  {LIGHTS_SCRIPT} isn't next to this script: no lights added")


def add_pose_library():
    """Add the Storage folders (Storage/<each of POSE_LIBRARY>, Galtis, Manny_A ...) to the Preferences' asset libraries
    (once), so their poses show in the Asset Browser and Pose Magic finds its libraries by name."""
    storage = workspace_path("storage", "")
    names = [POSE_LIBRARY] if isinstance(POSE_LIBRARY, str) else list(POSE_LIBRARY or ())
    names = [n for n in names if n]
    if not names or not storage:
        return
    for name in names:
        os.makedirs(os.path.join(storage, name), exist_ok=True)
    script = next((os.path.join(f, LIBRARIES_SCRIPT) for f in script_folders()
                   if os.path.isfile(os.path.join(f, LIBRARIES_SCRIPT))), None)
    if script:
        scope = {"__name__": "build_metahuman_rig_libraries"}
        with open(script, "r", encoding="utf-8") as fh:
            exec(compile(fh.read(), LIBRARIES_SCRIPT, "exec"), scope)
        scope["add_asset_libraries"](storage)
        return
    libraries = bpy.context.preferences.filepaths.asset_libraries
    for name in names:
        folder = os.path.join(storage, name)
        wanted = os.path.normcase(os.path.abspath(folder))
        known = next((lib for lib in libraries if os.path.normcase(os.path.abspath(bpy.path.abspath(lib.path))) == wanted), None)
        if known is not None:
            print(f"  pose library: '{known.name}' ({folder})")
            continue
        try:
            bpy.ops.preferences.asset_library_add(directory=folder + os.sep)
            libraries[-1].name = name
            print(f"  pose library: added '{name}' ({folder}) to the Asset Browser's libraries "
                  "(kept when Blender closes if Auto-Save Preferences is on)")
        except Exception as err:
            print(f"  pose library: couldn't add {folder} ({err}) - add it in Edit > Preferences > File Paths > Asset Libraries")


add_panel()
add_lights()
add_pose_library()
rig.data.pose_position = "POSE"
for obj in bpy.context.view_layer.objects:
    obj.select_set(False)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
if CHARACTER_BLEND and NAME_FROM_METAHUMAN:
    if rig.get("sgm_metahuman"):
        CHARACTER_BLEND = os.path.join(os.path.dirname(CHARACTER_BLEND), rig["sgm_metahuman"] + ".blend")
    else:
        print(f"  the MetaHuman's name isn't known: saving as {os.path.basename(CHARACTER_BLEND)}")
if CHARACTER_BLEND:
    os.makedirs(os.path.dirname(CHARACTER_BLEND), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=CHARACTER_BLEND, relative_remap=False)
    print(f"[saved] {CHARACTER_BLEND}")
print("Next: run import_unreal_take.py in this file for each take.")
