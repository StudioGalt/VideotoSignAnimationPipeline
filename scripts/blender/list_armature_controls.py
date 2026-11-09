import bpy, json, os

# Lists every bone of an armature with what controls it: its parent, its constraints (and the
# bones or objects they point at) and its drivers. Then it follows those links upward, bone by
# bone, to the bones at the top of each chain - the ones that are animated by hand and that a
# retarget has to key.
#
# It changes nothing in the file. Output:
#   <armature>_bones.txt    the report, for reading
#   <armature>_bones.json   the same facts as data
# both next to the .blend (or in OUTPUT_FOLDER), and the report in the Text Editor as well.
#
# Run it from the Text Editor with the avatar file (or a filed take) open.

# --- config ---
ARMATURE = ""        # name of the armature object. "" = the active object if it is the avatar's armature, else the
                     # armature with the most bones. Imported takes (*_take, *_face_board) are never picked
                     # automatically; name one here to report on it.
OUTPUT_FOLDER = ""   # "" = next to the .blend
SKIP_SUFFIXES = ("_take", "_face_board")     # armatures this pipeline imports; never picked automatically
MAX_DEPTH = 12       # how far a chain of control is followed

# MetaHuman body bones. A bone of the armature with one of these names (or the name of a bone in an
# imported *_take skeleton in this file) is listed in the "bones named like MetaHuman bones" section.
METAHUMAN_BONES = ["root", "pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05", "neck_01", "neck_02", "head"]
for _side in ("l", "r"):
    METAHUMAN_BONES += [f"{b}_{_side}" for b in (
        "clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball",
        "upperarm_twist_01", "upperarm_twist_02", "lowerarm_twist_01", "lowerarm_twist_02",
        "thigh_twist_01", "thigh_twist_02", "calf_twist_01", "calf_twist_02",
        "thumb_01", "thumb_02", "thumb_03",
        "index_metacarpal", "index_01", "index_02", "index_03",
        "middle_metacarpal", "middle_01", "middle_02", "middle_03",
        "ring_metacarpal", "ring_01", "ring_02", "ring_03",
        "pinky_metacarpal", "pinky_01", "pinky_02", "pinky_03")]

_lines = []


def out(text=""):
    _lines.append(str(text))
    print(text)


# --- which armature ---
def pick_armature():
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.pose]
    if ARMATURE:
        obj = bpy.data.objects.get(ARMATURE)
        if obj is None or obj.type != "ARMATURE":
            raise RuntimeError(f"ARMATURE: there is no armature object named '{ARMATURE}'. Armatures in this file: "
                               + (", ".join(o.name for o in armatures) or "none"))
        return obj, armatures
    own = [o for o in armatures if not o.name.lower().endswith(tuple(s.lower() for s in SKIP_SUFFIXES))]
    active = bpy.context.view_layer.objects.active
    if active is not None and active in own:
        return active, armatures
    if not own:
        raise RuntimeError("No armature to report on. Select the avatar's armature, or set ARMATURE at the top.")
    return max(own, key=lambda o: len(o.pose.bones)), armatures


# --- constraints and drivers ---
def constraint_targets(armature, constraint):
    """
    [(role, object name, bone name or "", weight)] for everything a constraint points at. Found from
    the constraint's own properties, so every constraint type is covered: 'target' + 'subtarget',
    'pole_target' + 'pole_subtarget', and the target list of an Armature constraint.
    """
    found = []
    for prop in constraint.bl_rna.properties:
        if prop.type != "POINTER" or prop.identifier in ("rna_type", "id_data"):
            continue
        value = getattr(constraint, prop.identifier, None)
        if isinstance(value, bpy.types.Object):
            if prop.identifier == "space_object":
                bone = getattr(constraint, "space_subtarget", "")
            else:
                bone = getattr(constraint, prop.identifier.replace("target", "subtarget"), "") \
                    if "target" in prop.identifier else ""
            found.append((prop.identifier, value.name, bone or "", None))
    for entry in getattr(constraint, "targets", None) or []:
        target = getattr(entry, "target", None)
        if isinstance(target, bpy.types.Object):
            found.append(("target", target.name, getattr(entry, "subtarget", "") or "", getattr(entry, "weight", None)))
    return found


def constraint_record(armature, constraint):
    live = (not constraint.mute) and constraint.influence > 0.0
    record = {"type": constraint.type, "name": constraint.name, "active": live,
              "influence": round(constraint.influence, 4), "muted": bool(constraint.mute), "targets": []}
    for key in ("owner_space", "target_space", "mix_mode", "chain_count", "use_tail", "head_tail"):
        if hasattr(constraint, key):
            value = getattr(constraint, key)
            record[key] = round(value, 4) if isinstance(value, float) else value
    for role, obj_name, bone, weight in constraint_targets(armature, constraint):
        record["targets"].append({"role": role, "object": obj_name, "bone": bone,
                                  "same_armature": obj_name == armature.name,
                                  **({"weight": round(weight, 4)} if weight is not None else {})})
    return record


def bone_drivers(armature):
    """{bone name: [{"property", "expression", "from": [..]}]} for the drivers on pose bone properties."""
    found = {}
    animation = armature.animation_data
    for curve in (animation.drivers if animation else []):
        path = curve.data_path
        if not path.startswith('pose.bones["'):
            continue
        bone = path.split('"')[1]
        sources = []
        for variable in curve.driver.variables:
            for target in variable.targets:
                owner = target.id.name if target.id is not None else "?"
                if getattr(target, "bone_target", ""):
                    sources.append(f"{owner}:{target.bone_target}")
                elif target.data_path:
                    sources.append(f"{owner}:{target.data_path}")
                else:
                    sources.append(owner)
        prop = path.split('"]', 1)[1].lstrip(".")
        if prop.split(".")[-1] in ("location", "rotation_euler", "rotation_quaternion", "scale"):
            prop += f"[{curve.array_index}]"
        found.setdefault(bone, []).append({"property": prop, "expression": curve.driver.expression,
                                           "type": curve.driver.type, "from": sorted(set(sources))})
    return found


def driver_bones(armature, drivers):
    """Bones of this armature that a bone's drivers read from."""
    bones = set()
    for entry in drivers:
        for source in entry["from"]:
            owner, _, rest = source.partition(":")
            if owner != armature.name:
                continue
            if rest in armature.pose.bones:
                bones.add(rest)
            elif rest.startswith('pose.bones["'):
                bones.add(rest.split('"')[1])
    return bones


# --- reading the armature ---
def read_armature(armature):
    drivers = bone_drivers(armature)
    bones = {}
    for pose_bone in armature.pose.bones:
        bone = pose_bone.bone
        constraints = [constraint_record(armature, c) for c in pose_bone.constraints]
        controllers, outside = [], []
        for record in constraints:
            if not record["active"]:
                continue
            for target in record["targets"]:
                if target["same_armature"] and target["bone"]:
                    if target["bone"] not in controllers and target["bone"] != pose_bone.name:
                        controllers.append(target["bone"])
                elif not target["same_armature"]:
                    label = target["object"] + (":" + target["bone"] if target["bone"] else "")
                    if label not in outside:
                        outside.append(label)
        driven_by = sorted(driver_bones(armature, drivers.get(pose_bone.name, [])) - {pose_bone.name})
        bones[pose_bone.name] = {
            "parent": bone.parent.name if bone.parent else None,
            "connected": bool(bone.use_connect),
            "deform": bool(bone.use_deform),
            "collections": [c.name for c in getattr(bone, "collections", [])],
            "custom_shape": pose_bone.custom_shape.name if pose_bone.custom_shape else None,
            "rotation_mode": pose_bone.rotation_mode,
            "constraints": constraints,
            "drivers": drivers.get(pose_bone.name, []),
            "controlled_by": controllers,           # bones of this armature its active constraints point at
            "driver_bones": driven_by,              # bones of this armature its drivers read
            "outside": outside,                     # other objects (and their bones) its constraints point at
            "ik_from": [],                          # IK chains it is part of: the bone holding the constraint, and its targets
        }
    # An IK constraint sits on the last bone of its chain but also moves the bones above it.
    for name, info in list(bones.items()):
        for record in info["constraints"]:
            if record["type"] not in ("IK", "SPLINE_IK") or not record["active"]:
                continue
            targets = [t["bone"] for t in record["targets"] if t["same_armature"] and t["bone"]]
            count, above = record.get("chain_count", 0), info["parent"]
            steps = 1
            while above is not None and (count == 0 or steps < count):
                bones[above]["ik_from"].append({"through": name, "targets": targets})
                for target in targets:
                    if target not in bones[above]["controlled_by"] and target != above:
                        bones[above]["controlled_by"].append(target)
                above, steps = bones[above]["parent"], steps + 1
    for name, info in bones.items():
        info["controls"] = sorted(other for other, o in bones.items()
                                  if name in o["controlled_by"] or name in o["driver_bones"])
    return bones


def chain_tops(bones, name, seen=None, depth=0):
    """
    The bones at the top of `name`'s chain of control: follow the constraints of each bone to the
    bones they point at, until a bone is reached that no constraint in this armature controls.
    (Drivers are listed with each bone but not followed: they mostly read switches.)
    """
    seen = set() if seen is None else seen
    if name in seen or depth > MAX_DEPTH or name not in bones:
        return []
    seen.add(name)
    above = bones[name]["controlled_by"]
    if not above:
        return [name]
    tops = []
    for controller in above:
        for top in chain_tops(bones, controller, seen, depth + 1):
            if top not in tops:
                tops.append(top)
    return tops


def chain_lines(bones, name, indent, seen=None, depth=0):
    """The chain of control above `name`, one bone per line with the constraint that makes the link."""
    seen = set() if seen is None else seen
    lines = []
    if name not in bones or depth > MAX_DEPTH:
        return lines
    info = bones[name]
    for record in info["constraints"]:
        for target in record["targets"]:
            label = target["bone"] if target["same_armature"] else target["object"] + (":" + target["bone"] if target["bone"] else "")
            if not label:
                continue
            notes = [record["type"]]
            if target["role"] != "target":
                notes.append(target["role"])
            if "weight" in target:
                notes.append(f"weight {target['weight']:g}")
            if record["influence"] < 1.0:
                notes.append(f"influence {record['influence']:g}")
            if not record["active"]:
                notes.append("OFF")
            repeat = target["same_armature"] and target["bone"] in seen
            lines.append(f"{indent}<- {label}   [{', '.join(notes)}]"
                         + ("   (not in this armature)" if not target["same_armature"] else "")
                         + ("   (already shown)" if repeat else ""))
            if target["same_armature"] and record["active"] and not repeat and target["bone"] != name:
                seen.add(target["bone"])
                lines += chain_lines(bones, target["bone"], indent + "   ", seen, depth + 1)
    for entry in info["ik_from"]:
        for bone in entry["targets"]:
            repeat = bone in seen
            lines.append(f"{indent}<- {bone}   [IK chain, constraint on {entry['through']}]"
                         + ("   (already shown)" if repeat else ""))
            if not repeat:
                seen.add(bone)
                lines += chain_lines(bones, bone, indent + "   ", seen, depth + 1)
    for bone in info["driver_bones"]:
        lines.append(f"{indent}<- {bone}   [driver]")
    return lines


def kind_of(info):
    """driven = something in the rig moves it; control = moves other bones and nothing moves it; free = neither."""
    if info["controlled_by"] or info["driver_bones"] or info["outside"] or info["ik_from"]:
        return "driven"
    return "control" if info["controls"] else "free"


# --- the report ---
def report(armature, armatures):
    bones = read_armature(armature)
    for name, info in bones.items():
        info["kind"] = kind_of(info)
        info["tops"] = chain_tops(bones, name)

    reference = {b.lower() for b in METAHUMAN_BONES}
    skeletons = [o for o in armatures if o is not armature and o.name.lower().endswith("_take")]
    for skeleton in skeletons:
        reference.update(b.name.lower() for b in skeleton.pose.bones)

    counts = {k: sum(1 for i in bones.values() if i["kind"] == k) for k in ("driven", "control", "free")}
    out(f"Armature: {armature.name}   ({bpy.data.filepath or 'unsaved file'})")
    out(f"Bones: {len(bones)}   deform: {sum(1 for i in bones.values() if i['deform'])}   "
        f"with constraints: {sum(1 for i in bones.values() if i['constraints'])}   "
        f"with drivers: {sum(1 for i in bones.values() if i['drivers'])}")
    out(f"  driven  {counts['driven']:>4}   a constraint or driver moves them")
    out(f"  control {counts['control']:>4}   they move other bones and nothing in the rig moves them")
    out(f"  free    {counts['free']:>4}   no constraint points at them or from them (they follow their parent, or are keyed directly)")
    others = [o.name for o in armatures if o is not armature]
    if others:
        out(f"Other armatures in this file: {', '.join(others)}")
    used = {}
    for info in bones.values():
        for record in info["constraints"]:
            used[record["type"]] = used.get(record["type"], 0) + 1
    if used:
        out("Constraint types: " + ", ".join(f"{k} {v}" for k, v in sorted(used.items(), key=lambda item: -item[1])))

    # 1. MetaHuman-named bones and what to retarget to
    matches = [n for n in bones if n.lower() in reference]
    out()
    out("=" * 100)
    out(f"1. BONES NAMED LIKE METAHUMAN BONES ({len(matches)})  ->  the bones at the top of their chain of control")
    out("=" * 100)
    if skeletons:
        out(f"(names compared with the MetaHuman bone list and with the skeleton '{skeletons[0].name}')")
    if not matches:
        out("None of this armature's bones has a MetaHuman bone name.")
    width = max([len(n) for n in matches] + [10])
    for name in matches:
        info = bones[name]
        tops = [t for t in info["tops"] if t != name]
        if tops:
            target = ", ".join(tops)
        elif info["outside"]:
            target = "(nothing in this armature)"
        elif info["driver_bones"]:
            target = "itself; drivers read " + ", ".join(info["driver_bones"])
        else:
            parent = info["parent"]
            above = [t for t in bones[parent]["tops"] if t != parent] if parent else []
            target = "itself - no constraint moves it; parent " + (parent or "none") \
                + (f", which is moved by {', '.join(above)}" if above else "")
        if info["outside"]:
            target += "   + other objects: " + ", ".join(info["outside"])
        out(f"  {name:<{width}}  ->  {target}")
    out()
    out("The chains behind those:")
    for name in matches:
        info = bones[name]
        lines = chain_lines(bones, name, "      ", {name})
        if lines:
            out(f"  {name}" + ("   [deform]" if info["deform"] else ""))
            for line in lines:
                out(line)

    # 2. controls
    out()
    out("=" * 100)
    out(f"2. CONTROL BONES ({counts['control']})  -  nothing in the rig moves them; these are what gets keyed")
    out("=" * 100)
    for name, info in bones.items():
        if info["kind"] != "control":
            continue
        below = sorted(other for other, o in bones.items() if name in o["tops"] and other != name)
        out(f"  {name}   (parent: {info['parent'] or 'none'}"
            + (f"; collections: {', '.join(info['collections'])}" if info["collections"] else "") + ")")
        if below:
            out(f"      moves {len(below)}: {', '.join(below[:14])}" + (" ..." if len(below) > 14 else ""))
        else:
            out(f"      read by drivers on: {', '.join(info['controls'][:14])}" + (" ..." if len(info["controls"]) > 14 else ""))

    # 3. every bone, in hierarchy order
    out()
    out("=" * 100)
    out(f"3. EVERY BONE ({len(bones)}), parents before children")
    out("=" * 100)
    children = {}
    for name, info in bones.items():
        children.setdefault(info["parent"], []).append(name)

    def walk(name, depth):
        info = bones[name]
        tags = [info["kind"]] + (["deform"] if info["deform"] else []) + (["shape"] if info["custom_shape"] else [])
        out(f"{'  ' * depth}{name}   [{', '.join(tags)}]"
            + (f"   {{{', '.join(info['collections'])}}}" if info["collections"] else ""))
        pad = "  " * depth + "      "
        for record in info["constraints"]:
            targets = ", ".join(
                (t["bone"] if t["same_armature"] and t["bone"] else t["object"] + (":" + t["bone"] if t["bone"] else ""))
                + (f" ({t['role']})" if t["role"] != "target" else "") + (f" x{t['weight']:g}" if "weight" in t else "")
                for t in record["targets"]) or "no target"
            extra = []
            if record["influence"] < 1.0:
                extra.append(f"influence {record['influence']:g}")
            if record["muted"]:
                extra.append("muted")
            if "chain_count" in record:
                extra.append(f"chain {record['chain_count']}")
            if record.get("owner_space") and record.get("target_space") \
                    and (record["owner_space"], record["target_space"]) != ("WORLD", "WORLD"):
                extra.append(f"space {record['target_space'].lower()} -> {record['owner_space'].lower()}")
            out(f"{pad}{record['type']} '{record['name']}' -> {targets}" + (f"   [{', '.join(extra)}]" if extra else ""))
        for entry in info["ik_from"]:
            out(f"{pad}in the IK chain of {entry['through']} -> {', '.join(entry['targets']) or 'no bone target'}")
        for entry in info["drivers"]:
            out(f"{pad}driver on {entry['property']} <- {', '.join(entry['from']) or 'no variables'}"
                + (f"   [{entry['expression']}]" if entry["type"] == "SCRIPTED" and entry["expression"] else ""))
        for child in children.get(name, []):
            walk(child, depth + 1)

    for root_bone in children.get(None, []):
        walk(root_bone, 0)
    return bones


def output_folder():
    if OUTPUT_FOLDER:
        return OUTPUT_FOLDER
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return bpy.app.tempdir


# --- run ---
armature, armatures = pick_armature()
bones = report(armature, armatures)

folder = output_folder()
stem = bpy.path.clean_name(armature.name) + "_bones"
text_path, json_path = os.path.join(folder, stem + ".txt"), os.path.join(folder, stem + ".json")
try:
    os.makedirs(folder, exist_ok=True)
    with open(text_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"armature": armature.name, "file": bpy.data.filepath, "bones": bones}, fh, indent=1)
    print(f"[saved] {text_path}")
    print(f"[saved] {json_path}")
except OSError as e:
    print(f"could not write the report files: {e}")
block = bpy.data.texts.get(stem + ".txt") or bpy.data.texts.new(stem + ".txt")
block.clear()
block.write("\n".join(_lines) + "\n")
print(f"[text] the report is also in the Text Editor as '{block.name}'")
