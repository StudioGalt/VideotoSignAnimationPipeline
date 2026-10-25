import bpy, json, os, re

# The MetaHuman's own look in Blender, from what export_metahuman.py writes out of Unreal:
#   - skin, eyes and teeth get materials built from the exported textures (colour, normal, roughness),
#     on the body, the outfit, the FBX face and the DNA add-on's head (which shows grey without them)
#   - the hair cards of the grooms (Hair, Eyebrows ...) are imported and attached to the head
# Anything without textures keeps the plain material apply_plain_materials.py gave it.
#
# build_metahuman_rig.py runs this after the plain materials. It can also be run on its own in the
# character file (MetaHuman_character.blend) or in a take file: it finds the newest export in
# MH_EXPORT_FOLDER. Running it again changes nothing that is already done.

MH_EXPORT_FOLDER = None                # the export, or the folder above it. None = paths.metahuman_export in
                                       #   pipeline_config.json (build_metahuman_rig.py passes its own)
RIG_NAME = "MH_rig"                    # the body armature (build_metahuman_rig.py)
FACE_SKELETON_NAME = "MH_face_skeleton"
CLOTHING_SKELETON_NAME = "MH_clothing_skeleton"
BODY_SCALE = 100.0                     # the size the FBX files are imported at (as in build_metahuman_rig.py)
IMPORT_OPS = ("wm.fbx_import", "import_scene.fbx")
TEXTURES = True
HAIR = True
HAIR_BONE = "head"                     # the hair cards move with this bone of the rig
HAIR_COLOR = (0.09, 0.055, 0.035)      # used when the groom's material has no melanin value to go by
HAIR_ROUGHNESS = 0.45

CLOTHING_COLOR = (0.25, 0.3, 0.38)    # the outfit's colour when its Unreal material names none (diffuse_color_1/2)
CLOTHING_COLORS = {}                   # a colour per garment, by a word in its material name, over Unreal's own, e.g.
                                       # {"shirt": (0.6, 0.6, 0.62), "short": (0.02, 0.02, 0.025)}
DARKEST = 0.025                        # colours are kept at least this light, so folds still show on black fabric
IRIS_UV_RADIUS = 0.15                  # the iris's size on the eyeball's UVs (MetaHuman eyes: 0.15)
MATERIAL_VERSION = 3                   # materials made by an older version of this script are rebuilt

# Which texture does what. MetaHuman materials hold many textures: the baked maps ("Basecolor Baked VT",
# "Normal Baked VT", "SRMF Baked VT"), and placeholders (T_Flat_*) and detail maps that Blender doesn't use.
PLACEHOLDER = re.compile(r"^(t_flat_|white|black|grey|gray|default|t_default)", re.I)
UNUSED = re.compile(r"(\b|_)(cm|wm)\d|delta|animated|detail|micro|macro|pill|variation|flow|pores|n3c|bent|cavity|"
                    r"scatter|mask|spectrum|print|stitch|wrinkle|lod baked|distort|fuzz", re.I)


def pick(entry, folder, test):
    """The texture file for the first matching parameter, the baked (and virtual-texture) map first."""
    found = []
    for param, file in (entry.get("textures") or {}).items():
        name = param.lower()
        path = os.path.join(folder, file)
        if not test(name) or UNUSED.search(name) or PLACEHOLDER.match(os.path.basename(file)) or not os.path.isfile(path):
            continue
        found.append(("baked" not in name, " vt" not in name + " ", name, path))
    return sorted(found)[0][3] if found else None


HIDDEN = ("occlusion", "lacrimal", "tear", "cartilage", "eyeshell", "eye_shell", "eyeedge", "eye_edge", "saliva")
                                       # see-through overlays: they stay hidden (apply_plain_materials.py)
# The DNA add-on's head is made of separate meshes; which of the face's materials each one takes
DNA_PARTS = (  # (words in the DNA mesh's name, words in the face material's name, words it must not have)
    (("eyelash",), ("eyelash", "lash"), ()),
    (("teeth",), ("teeth", "tooth"), ()),
    (("eyeleft", "eye_l", "eyel_"), ("eye",), ("lash", "occlusion", "shell", "edge", "lacrimal", "_r", "right")),
    (("eyeright", "eye_r", "eyer_"), ("eye",), ("lash", "occlusion", "shell", "edge", "lacrimal", "_l", "left")),
    (("head",), ("head", "skin", "face"), ("lash", "teeth", "eye", "brow")),
)


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


def export_folder(folder):
    """The folder with metahuman_export.json: `folder`, else its most recently exported sub-folder."""
    if os.path.isfile(os.path.join(folder, "metahuman_export.json")):
        return folder
    subs = []
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        rec = os.path.join(folder, name, "metahuman_export.json")
        if os.path.isfile(rec):
            subs.append((os.path.getmtime(rec), os.path.join(folder, name)))
    return sorted(subs)[-1][1] if subs else None


def short(name):
    return re.sub(r"\.\d{3}$", "", name or "")


def image(path, non_color):
    img = bpy.data.images.load(path, check_existing=True)
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
    return img


def new_material(name):
    """The material `name`, empty but for a Principled BSDF and its output (rebuilt when an older version made it)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    out.location, bsdf.location = (300, 0), (0, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat["sgm_version"] = MATERIAL_VERSION
    return mat, nodes, mat.node_tree.links, bsdf


def mix_rgb(nodes, links, a, b, factor=None, blend="MIX", location=(-400, 200)):
    """A Mix node on colours: a, b (sockets or RGBA tuples), factor (socket or value). Returns its colour output."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type, mix.location = "RGBA", blend, location
    ins = [i for i in mix.inputs if i.type == "RGBA"]
    for sock, value in zip(ins, (a, b)):
        if isinstance(value, tuple):
            sock.default_value = value
        else:
            links.new(value, sock)
    if factor is None or isinstance(factor, float):
        mix.inputs["Factor"].default_value = 1.0 if factor is None else factor
    else:
        links.new(factor, mix.inputs["Factor"])
    return next(o for o in mix.outputs if o.type == "RGBA")


def tex(nodes, path, non_color, location):
    node = nodes.new("ShaderNodeTexImage")
    node.image, node.location = image(path, non_color), location
    return node


def add_normal(nodes, links, bsdf, path):
    if path:
        t = tex(nodes, path, True, (-900, -350))
        n = nodes.new("ShaderNodeNormalMap")
        n.location = (-400, -350)
        links.new(t.outputs["Color"], n.inputs["Color"])
        links.new(n.outputs["Normal"], bsdf.inputs["Normal"])


def add_packed_roughness(nodes, links, bsdf, path, plain):
    """Roughness from a packed map (SRMF / SRM: specular, roughness, ... - roughness is green) or a plain one."""
    if not path:
        return
    t = tex(nodes, path, True, (-900, 0))
    if plain:
        links.new(t.outputs["Color"], bsdf.inputs["Roughness"])
        return
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (-400, 0)
    links.new(t.outputs["Color"], sep.inputs["Color"])
    links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])


def eye_material(name, entry, folder):
    """Sclera map all over, the iris map inside IRIS_UV_RADIUS of the middle of the eyeball's UVs."""
    sclera = pick(entry, folder, lambda n: "sclera" in n and ("basecolor" in n or "color" in n))
    iris = pick(entry, folder, lambda n: "iris" in n and ("basecolor" in n or n.strip() == "iris"))
    if not sclera and not iris:
        return None
    mat, nodes, links, bsdf = new_material(name)
    bsdf.inputs["Roughness"].default_value = 0.1
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1600, 0)
    color = None
    if sclera:
        s_tex = tex(nodes, sclera, False, (-900, 300))
        links.new(uv.outputs["UV"], s_tex.inputs["Vector"])
        color = s_tex.outputs["Color"]
    if iris:
        # iris UV = (uv - 0.5) / (2 r) + 0.5, so the iris map fills the circle of radius r
        shift = nodes.new("ShaderNodeVectorMath"); shift.operation = "SUBTRACT"; shift.location = (-1400, -200)
        shift.inputs[1].default_value = (0.5, 0.5, 0.0)
        scale = nodes.new("ShaderNodeVectorMath"); scale.operation = "SCALE"; scale.location = (-1200, -200)
        scale.inputs["Scale"].default_value = 1.0 / (2.0 * IRIS_UV_RADIUS)
        back = nodes.new("ShaderNodeVectorMath"); back.operation = "ADD"; back.location = (-1000, -200)
        back.inputs[1].default_value = (0.5, 0.5, 0.0)
        links.new(uv.outputs["UV"], shift.inputs[0]); links.new(shift.outputs[0], scale.inputs[0])
        links.new(scale.outputs[0], back.inputs[0])
        i_tex = tex(nodes, iris, False, (-800, -200))
        i_tex.extension = "CLIP"
        links.new(back.outputs[0], i_tex.inputs["Vector"])
        dist = nodes.new("ShaderNodeVectorMath"); dist.operation = "LENGTH"; dist.location = (-1000, -450)
        links.new(shift.outputs[0], dist.inputs[0])
        edge = nodes.new("ShaderNodeMapRange"); edge.location = (-800, -450)
        edge.inputs["From Min"].default_value = IRIS_UV_RADIUS * 0.92
        edge.inputs["From Max"].default_value = IRIS_UV_RADIUS
        edge.inputs["To Min"].default_value, edge.inputs["To Max"].default_value = 1.0, 0.0
        links.new(dist.outputs["Value"], edge.inputs["Value"])
        if color is None:
            color = i_tex.outputs["Color"]
        else:
            color = mix_rgb(nodes, links, color, i_tex.outputs["Color"], edge.outputs["Result"])
    links.new(color, bsdf.inputs["Base Color"])
    print(f"    {entry.get('material')}: eye - sclera {os.path.basename(sclera or '-')}, iris {os.path.basename(iris or '-')}")
    return mat


def fabric_color(entry):
    """An outfit material's colour: CLOTHING_COLORS by name, else the material's diffuse_color_1 (or _2, when
    B_useAcolor_1or2 says 2), else CLOTHING_COLOR - kept at least DARKEST."""
    name = (entry.get("material") or "").lower()
    color = next((tuple(c) for word, c in CLOTHING_COLORS.items() if word.lower() in name), None)
    if color is None:
        vectors = {k.lower(): v for k, v in (entry.get("vectors") or {}).items()}
        which = int(round((entry.get("scalars") or {}).get("B_useAcolor_1or2", 1)))
        found = vectors.get(f"diffuse_color_{which}") or vectors.get("diffuse_color_1")
        color = tuple(found[:3]) if found else CLOTHING_COLOR
    return tuple(max(DARKEST, float(c)) for c in color[:3])


def textured_material(entry, folder):
    """A material from one exported material slot's textures: skin, teeth, eyes, outfit. None = keep it plain."""
    name = "MH_tex_" + (entry.get("material") or entry.get("slot") or "material")
    old = bpy.data.materials.get(name)
    if old is not None and old.get("sgm_version") == MATERIAL_VERSION:
        return old                                    # made on an earlier run
    base = (entry.get("base_material") or "").lower()
    if "eye" in base and "eyeball" in base or any("sclera" in p.lower() for p in entry.get("textures") or {}):
        return eye_material(name, entry, folder)
    color = (pick(entry, folder, lambda n: any(w in n for w in ("basecolor", "base color", "albedo", "diffuse")) or n.endswith("color"))
             or pick(entry, folder, lambda n: "color" in n or "colour" in n))
    normal = pick(entry, folder, lambda n: "normal" in n)
    packed = pick(entry, folder, lambda n: n.split()[0] in ("srmf", "srm") or n.startswith(("srmf", "srm")))
    rough = None if packed else pick(entry, folder, lambda n: "rough" in n)
    ao = pick(entry, folder, lambda n: n.strip() == "ao" or "ambient" in n)
    fabric = "fabric" in base or "garment" in (entry.get("material") or "").lower()
    cloth = fabric_color(entry)
    if color is None and not fabric:
        print(f"    {entry.get('material')}: no colour map among {len(entry.get('textures') or {})} textures - left plain")
        return None
    mat, nodes, links, bsdf = new_material(name)
    if color:
        c = tex(nodes, color, False, (-900, 350))
        links.new(c.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (*cloth, 1.0)
        if "Sheen Weight" in bsdf.inputs:            # fabric fuzz
            bsdf.inputs["Sheen Weight"].default_value = 0.3
        if ao:                                        # the garment's folds, from its AO map, darken the colour
            a_tex = tex(nodes, ao, True, (-900, 350))
            links.new(mix_rgb(nodes, links, (*cloth, 1.0), a_tex.outputs["Color"], blend="MULTIPLY"),
                      bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.8
    add_packed_roughness(nodes, links, bsdf, packed or rough, plain=packed is None)
    add_normal(nodes, links, bsdf, normal)
    mat["sgm_unreal_material"] = entry.get("material", "")
    print(f"    {entry.get('material')}: colour {os.path.basename(color) if color else tuple(round(c, 3) for c in cloth)}"
          + (f", AO {os.path.basename(ao)}" if ao and not color else "")
          + (f", normal {os.path.basename(normal)}" if normal else "")
          + (f", roughness {os.path.basename(packed or rough)}" if (packed or rough) else ""))
    return mat


def armature(name):
    obj = bpy.data.objects.get(name)
    return obj if obj is not None and obj.type == "ARMATURE" else None


def meshes_of(arm):
    if arm is None:
        return []
    return [o for o in bpy.data.objects if o.type == "MESH" and not o.name.startswith("MH_hair_")   # not the hair cards
            and (o.parent is arm or any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers))]


def is_face_armature(obj):
    return obj.type == "ARMATURE" and any(b.name.startswith("FACIAL_") for b in obj.data.bones)


def apply_to_object(obj, slots, made, folder):
    """Give the object's material slots the textured materials of the export's slots (matched by the Unreal
    material's name, else by slot order). Returns the number of slots changed."""
    by_name = {}
    for entry in slots:
        for key in (entry.get("material"), entry.get("slot")):
            if key:
                by_name.setdefault(key.lower(), entry)
    remembered = json.loads(obj.get("sgm_unreal_slots", "[]"))
    changed = 0
    names = []
    for i, slot in enumerate(obj.material_slots):
        current = slot.material.name if slot.material else ""
        original = remembered[i] if i < len(remembered) else ("" if current.startswith(("MH_plain_", "MH_tex_")) else short(current))
        names.append(original)
        if current.startswith("MH_plain_hidden") or (current.startswith("MH_tex_")
                                                     and slot.material.get("sgm_version") == MATERIAL_VERSION):
            continue
        entry = by_name.get(original.lower()) if original else None
        if entry is None and len(obj.material_slots) == len(slots):
            entry = slots[i]                          # an earlier run's plain material: the slot order is Unreal's
        if entry is None or any(w in (entry.get("material", "") + " " + entry.get("slot", "") + " " + original).lower()
                                for w in HIDDEN):
            continue
        key = entry.get("material") or entry.get("slot")
        if key not in made:
            made[key] = textured_material(entry, folder)
        if made[key] is not None and slot.material != made[key]:
            slot.material = made[key]
            changed += 1
    if any(names):
        obj["sgm_unreal_slots"] = json.dumps(names)
    return changed


def apply_textures(record, folder):
    """Textured materials on the body, outfit and FBX face (by slot) and the DNA head (by part)."""
    materials, made, report = record.get("materials") or {}, {}, []
    if not materials:
        print("  textures: the export has no material list - run the new export_metahuman.py in Unreal")
        return 0
    parts = [("body", armature(RIG_NAME)), ("face", armature(FACE_SKELETON_NAME))]
    parts += [(p, armature(CLOTHING_SKELETON_NAME if p == "clothing" else f"{CLOTHING_SKELETON_NAME}_{p.split('_')[-1]}"))
              for p in materials if p.startswith("clothing")]
    total = 0
    for part, arm in parts:
        for obj in meshes_of(arm):
            n = apply_to_object(obj, materials.get(part, []), made, folder)
            total += n
            if n:
                report.append(f"{obj.name} {n}")
    face_slots = materials.get("face", [])
    for arm in [o for o in bpy.data.objects if is_face_armature(o) and not o.name.startswith(FACE_SKELETON_NAME)]:
        for obj in meshes_of(arm):
            label = re.sub(r"^.*?_head_(?=.)", "", obj.name.lower().replace(" ", ""))   # TestMetahuman_Head_teeth_.. -> teeth_..
            if any(w in label for w in HIDDEN):
                hidden = bpy.data.materials.get("MH_plain_hidden")
                for slot in obj.material_slots:          # an earlier version put the skin on these
                    if slot.material and slot.material.name.startswith("MH_tex_") and hidden is not None:
                        slot.material = hidden
                        total += 1
                continue
            for mesh_words, mat_words, without in DNA_PARTS:
                if not any(w in label for w in mesh_words):
                    continue
                entry = next((e for e in face_slots
                              if any(w in (e.get("material", "") + " " + e.get("slot", "")).lower() for w in mat_words)
                              and not any(w in (e.get("material", "") + " " + e.get("slot", "")).lower() for w in without)
                              and e.get("textures")), None)
                if entry is None:
                    break
                key = entry.get("material") or entry.get("slot")
                if key not in made:
                    made[key] = textured_material(entry, folder)
                if made[key] is not None:
                    for slot in obj.material_slots:
                        current = slot.material
                        if current != made[key] and not (current and current.name.startswith("MH_plain_hidden")):
                            slot.material = made[key]
                            total += 1
                    if not obj.material_slots:
                        obj.data.materials.append(made[key])
                        total += 1
                    report.append(f"{obj.name} <- {key}")
                break
    built = sorted(k for k, m in made.items() if m is not None)
    print(f"  textures: {total} material slot(s) now use Unreal's textures ({len(built)} materials: "
          f"{', '.join(built[:8])}{' ...' if len(built) > 8 else ''})")
    without = sorted(k for k, m in made.items() if m is None)
    if without:
        print(f"    no colour texture, so left plain: {', '.join(without[:10])}")
    return total


def import_fbx(path):
    before = set(bpy.data.objects)
    for idname in IMPORT_OPS:
        mod, op = idname.split(".")
        fn = getattr(getattr(bpy.ops, mod, None), op, None)
        if fn is None:
            continue
        try:
            try:
                fn("EXEC_DEFAULT", filepath=path, global_scale=BODY_SCALE)
            except TypeError:
                fn("EXEC_DEFAULT", filepath=path)
        except RuntimeError as e:
            print(f"    {idname}: {e}")
            continue
        new = [o for o in bpy.data.objects if o not in before]
        if new:
            return new
    return []


def hair_color(entry):
    """The hair's colour from the groom material's melanin / redness, else HAIR_COLOR."""
    scalars = {k.lower(): v for k, v in (entry.get("scalars") or {}).items()}
    melanin = next((scalars[k] for k in ("hairmelanin", "melanin") if k in scalars), None)
    if melanin is None:
        return HAIR_COLOR
    red = next((scalars[k] for k in ("hairredness", "redness", "melaninredness") if k in scalars), 0.0)
    m = max(0.0, min(1.0, melanin)) ** 0.5
    light, dark = (0.55 + 0.15 * red, 0.42, 0.28 - 0.1 * red), (0.02, 0.014, 0.01)
    return tuple(l + (d - l) * m for l, d in zip(light, dark))


def hair_material(entry, folder):
    """Hair cards: the colour from melanin, see-through outside the strands. The strands are read from the coverage
    map, or - when the groom has none (MetaHuman cards keep only Tangent and Attribute maps) - from the Attribute
    map: its root position (red, green) is set wherever a strand is, and its root-to-tip value (blue) fades the tips."""
    name = "MH_hair_" + entry.get("component", "hair")
    old = bpy.data.materials.get(name)
    if old is not None and old.get("sgm_version") == MATERIAL_VERSION:
        return old
    mat, nodes, links, bsdf = new_material(name)
    color = hair_color(entry)
    scalars = {k.lower(): v for k, v in (entry.get("scalars") or {}).items()}
    bsdf.inputs["Roughness"].default_value = scalars.get("hairroughness", HAIR_ROUGHNESS)
    files = {n.lower(): os.path.join(folder, f) for n, f in (entry.get("textures") or {}).items()
             if os.path.isfile(os.path.join(folder, f))}
    coverage = next((f for n, f in files.items() if any(w in n for w in ("coverage", "opacity", "alpha"))), None)
    attribute = next((f for n, f in files.items() if "attribute" in n), None)
    alpha = None
    if coverage:
        alpha = tex(nodes, coverage, True, (-900, -200)).outputs["Color"]
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    elif attribute:
        a_tex = tex(nodes, attribute, True, (-1200, -200))
        sep = nodes.new("ShaderNodeSeparateColor"); sep.location = (-900, -200)
        links.new(a_tex.outputs["Color"], sep.inputs["Color"])
        root = nodes.new("ShaderNodeMath"); root.operation = "MAXIMUM"; root.location = (-700, -150)
        links.new(sep.outputs["Red"], root.inputs[0]); links.new(sep.outputs["Green"], root.inputs[1])
        mask = nodes.new("ShaderNodeMath"); mask.operation = "GREATER_THAN"; mask.location = (-500, -150)
        mask.inputs[1].default_value = 0.002
        links.new(root.outputs[0], mask.inputs[0])
        tip = nodes.new("ShaderNodeMapRange"); tip.location = (-700, -400)       # 1 at the root, 0.3 at the tip
        tip.inputs["From Min"].default_value, tip.inputs["From Max"].default_value = 0.6, 1.0
        tip.inputs["To Min"].default_value, tip.inputs["To Max"].default_value = 1.0, 0.3
        links.new(sep.outputs["Blue"], tip.inputs["Value"])
        fade = nodes.new("ShaderNodeMath"); fade.operation = "MULTIPLY"; fade.location = (-300, -250)
        links.new(mask.outputs[0], fade.inputs[0]); links.new(tip.outputs["Result"], fade.inputs[1])
        alpha = fade.outputs[0]
        # a little darker at the roots
        shade = nodes.new("ShaderNodeMapRange"); shade.location = (-700, 200)
        shade.inputs["To Min"].default_value, shade.inputs["To Max"].default_value = 0.65, 1.15
        links.new(sep.outputs["Blue"], shade.inputs["Value"])
        val = nodes.new("ShaderNodeCombineColor"); val.location = (-500, 300)
        for ch in ("Red", "Green", "Blue"):
            links.new(shade.outputs["Result"], val.inputs[ch])
        links.new(mix_rgb(nodes, links, (*color, 1.0), val.outputs["Color"], None, "MULTIPLY", (-300, 200)),
                  bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        print(f"    {entry.get('component')}: no coverage or attribute map - the cards show as solid strips")
    if alpha is not None:
        links.new(alpha, bsdf.inputs["Alpha"])
        for attr, value in (("surface_render_method", "DITHERED"), ("blend_method", "HASHED")):
            try:
                setattr(mat, attr, value)
            except (AttributeError, TypeError):
                pass
    print(f"    {entry.get('component')}: hair colour {tuple(round(c, 3) for c in color)}, strands from "
          + ("its coverage map" if coverage else "its attribute map" if attribute else "nothing"))
    return mat


def import_hair(record, folder):
    """Import each groom's hair cards and attach them to the rig's head bone."""
    entries = record.get("hair")
    if entries is None:
        print("  hair: the export has no hair list - run the new export_metahuman.py in Unreal")
        return 0
    if not entries:
        print("  hair: none in the export (the Unreal log says why: no groom, or strands only)")
        return 0
    rig = armature(RIG_NAME)
    coll = bpy.data.collections.get("MetaHuman hair") or bpy.data.collections.new("MetaHuman hair")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    done = 0
    for entry in entries:
        name = f"MH_hair_{entry.get('component', 'hair')}_g{entry.get('group', 0)}"
        if bpy.data.objects.get(name) is not None:     # imported on an earlier run: only its material is renewed
            mat = hair_material(entry, folder)
            for obj in [o for o in bpy.data.objects if o.name == name or o.name.startswith(name + ".")]:
                if obj.type == "MESH":
                    obj.data.materials.clear()
                    obj.data.materials.append(mat)
            continue
        path = os.path.join(folder, entry.get("file", ""))
        if not os.path.isfile(path):
            print(f"  hair: {path} is missing")
            continue
        new = [o for o in import_fbx(path) if o.type == "MESH"]
        if not new:
            print(f"  hair: nothing imported from {os.path.basename(path)}")
            continue
        mat = hair_material(entry, folder)
        for i, obj in enumerate(new):
            obj.name = name if i == 0 else f"{name}.{i:03d}"
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            coll.objects.link(obj)
            if rig is not None and HAIR_BONE in rig.data.bones:
                world = obj.matrix_world.copy()
                obj.parent, obj.parent_type, obj.parent_bone = rig, "BONE", HAIR_BONE
                bpy.context.view_layer.update()
                obj.matrix_world = world
            done += 1
        print(f"  hair: {entry.get('component')} group {entry.get('group')} -> '{name}'"
              + (f", follows {RIG_NAME}'s '{HAIR_BONE}' bone" if rig is not None else " (no rig to attach it to)"))
    return done


def apply_unreal_materials():
    global MH_EXPORT_FOLDER
    if not MH_EXPORT_FOLDER:
        MH_EXPORT_FOLDER = workspace_path("metahuman_export", "")
    folder = export_folder(MH_EXPORT_FOLDER) if MH_EXPORT_FOLDER else None
    if folder is None:
        print(f"apply_unreal_materials: no metahuman_export.json in {MH_EXPORT_FOLDER} - run export_metahuman.py "
              "in Unreal first")
        return
    with open(os.path.join(folder, "metahuman_export.json"), "r", encoding="utf-8") as fh:
        record = json.load(fh)
    print(f"apply_unreal_materials: {folder}")
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    if TEXTURES:
        apply_textures(record, folder)
    if HAIR:
        import_hair(record, folder)


if __name__ != "build_metahuman_rig_unreal_materials":
    apply_unreal_materials()
