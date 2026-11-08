import bpy

# Plain viewport materials for the MetaHuman: skin, eyes (with an iris), teeth, mouth, hair and brows, clothing;
# the see-through eye and mouth overlays are hidden. Unreal's MetaHuman materials don't survive the FBX export
# (they come in black), and the DNA add-on's head material needs texture maps that the DNA export doesn't
# include (it shows grey), so every MetaHuman mesh gets one of these instead: body, outfit, FBX face and the
# DNA head. Works in Solid, Material Preview and Rendered shading.
# Run it in any file with the MetaHuman (build_metahuman_rig.py runs it). Running it again changes nothing.

KEEP_DNA_HEAD_MATERIALS = False   # True = leave the DNA add-on's own head materials (when you have its texture maps)

PLAIN = [  # (words in the material or object name, material name, colour RGBA, hidden)
    (("occlusion", "lacrimal", "tear", "cartilage", "eyeshell", "eye_shell", "eyeedge", "eye_edge", "saliva"),
     "MH_plain_hidden", (0.0, 0.0, 0.0, 0.0), True),
    (("eyelash", "lash", "brow", "hair", "beard", "mustache", "moustache", "peachfuzz", "fuzz"),
     "MH_plain_hair", (0.12, 0.08, 0.05, 1.0), False),
    (("eye",), "MH_plain_eye", (0.9, 0.88, 0.85, 1.0), False),
    (("teeth", "tooth"), "MH_plain_teeth", (0.9, 0.88, 0.8, 1.0), False),
    (("tongue", "mouth", "gum"), "MH_plain_mouth", (0.6, 0.3, 0.3, 1.0), False),
    (("cloth", "outfit", "shirt", "pant", "top", "shoe", "torso", "legs", "feet", "garment"),
     "MH_plain_clothing", (0.25, 0.3, 0.38, 1.0), False),
    ((), "MH_plain_skin", (0.78, 0.58, 0.48, 1.0), False),
]
IRIS = (0.22, 0.13, 0.07)     # eye colour; the iris and pupil are drawn round the middle of the eye's UVs


def iris(mat):
    """Sclera, iris and pupil, from the distance to the middle of the eye mesh's UVs."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    coords = nodes.new("ShaderNodeTexCoord")
    dist = nodes.new("ShaderNodeVectorMath")
    dist.operation = "DISTANCE"
    dist.inputs[1].default_value = (0.5, 0.5, 0.0)
    ramp = nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.01, 0.01, 0.01, 1.0)          # pupil
    els[1].position, els[1].color = 0.125, (0.9, 0.88, 0.85, 1.0)         # sclera
    for pos, col in ((0.045, (0.01, 0.01, 0.01, 1.0)), (0.05, (*IRIS, 1.0)), (0.11, (*IRIS, 1.0))):
        e = els.new(pos)
        e.color = col
    links.new(coords.outputs["UV"], dist.inputs[0])
    links.new(dist.outputs["Value"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.15


def plain_material(name, color, hidden):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        if name == "MH_plain_eye" and mat.node_tree and not any(n.type == "TEX_COORD" for n in mat.node_tree.nodes):
            iris(mat)                                 # made by an earlier version, without the iris
        return mat
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:                     # (nodes are always on from Blender 5)
        mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
        bsdf.inputs["Alpha"].default_value = color[3]
    mat.diffuse_color = color if not hidden else (0.0, 0.0, 0.0, 0.0)
    if hidden:
        for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(mat, attr, value)
            except (AttributeError, TypeError):
                pass
    if name == "MH_plain_eye":
        iris(mat)
    return mat


def is_face(obj):
    return obj.type == "ARMATURE" and any(b.name.startswith("FACIAL_") for b in obj.data.bones)


def metahuman_meshes():
    """Meshes moved by the MetaHuman rig, the skeletons that follow it (face, outfit) and a DNA head."""
    rigs = [o for o in bpy.data.objects if o.type == "ARMATURE" and (o.get("sgm_rig") == "metahuman"
            or o.get("sgm_follows_body") or is_face(o))]
    dna = {o for o in rigs if is_face(o) and not o.get("sgm_follows_body") and o.get("sgm_rig") != "metahuman"}
    found = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        owners = {m.object for m in obj.modifiers if m.type == "ARMATURE"} | {obj.parent}
        owners &= set(rigs)
        if not owners or (KEEP_DNA_HEAD_MATERIALS and owners <= dna):
            continue
        found.append(obj)
    return found


def apply_plain_materials(meshes=None):
    counts = {}
    for obj in meshes if meshes is not None else metahuman_meshes():
        for slot in obj.material_slots:
            if slot.material and slot.material.name.startswith(("MH_plain_", "MH_tex_", "MH_hair_")):
                continue                                  # done on an earlier run, or Unreal's textures / hair cards
            text = ((slot.material.name if slot.material else "") + " " + obj.name).lower()
            mat_text = (slot.material.name if slot.material else obj.name).lower()
            pick = None
            for words, name, color, hidden in PLAIN:
                if words and any(w in mat_text for w in words):
                    pick = (name, color, hidden)
                    break
            if pick is None:                              # the material's name says nothing: try the mesh's
                for words, name, color, hidden in PLAIN:
                    if not words or any(w in text for w in words):
                        pick = (name, color, hidden)
                        break
            slot.material = plain_material(*pick)
            counts[pick[0]] = counts.get(pick[0], 0) + 1
    print("  plain materials: " + (", ".join(f"{n[9:]} x{c}" for n, c in sorted(counts.items())) if counts
                                   else "nothing to change"))
    return counts


if __name__ != "build_metahuman_rig_materials":
    apply_plain_materials()
