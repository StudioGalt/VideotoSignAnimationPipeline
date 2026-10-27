"""
Export the MetaHuman itself (body and face meshes with their skeletons) for Blender. Run it once,
and again only when the MetaHuman changes. The takes are exported separately by
export_animations.py; this script gives Blender the character those takes are put on.

Writes, into EXPORT_FOLDER (default: the workspace's paths.metahuman_export/<blueprint name>/ from
pipeline_config.json, else <project>/Saved/MetaHumanExport/<blueprint name>/):
    <blueprint>_body.fbx     the body skeletal mesh with its skeleton (pelvis, spine, arms, fingers, legs)
    <blueprint>_face.fbx     the face skeletal mesh with its facial joints and morph targets
    textures/                the textures of the meshes' materials (skin colour, normal, roughness ...), as PNG
    hair/                    the hair cards of the blueprint's grooms (Hair, Eyebrows ...): card mesh FBX + textures
    metahuman_export.json    which assets were exported, with each material slot's textures and each groom's
                             cards, for the Blender scripts (build_metahuman_rig.py, apply_unreal_materials.py)

build_metahuman_rig.py in Blender reads this folder, puts the two meshes together and builds the
control rig on the body skeleton.

Which MetaHuman: METAHUMAN_BLUEPRINT (name or path), else the config's unreal.metahuman_blueprint,
else the MetaHuman blueprint found in the config's unreal.character_folder (/Game/MetaHumans). The body and face meshes are read from the
blueprint's "Body" and "Face" components; when that fails, from the skeletal meshes in its folder.

About the face: the FBX face mesh moves with the head, but its expressions need RigLogic, which an
FBX doesn't carry. So the head DNA is exported too (EXPORT_DNA, from the MetaHuman Character asset);
build_metahuman_rig.py loads it with the MetaHuman DNA add-on and attaches that head to the body.

Run in the Unreal Editor: File > Execute Python Script.
"""

import json
import os
import sys
import time

import unreal

# ------------------------------------------------------------------ settings
METAHUMAN_BLUEPRINT = None   # None = the config's unreal.metahuman_blueprint, else the MetaHuman in the character
                             # folder (/Game/MetaHumans); a name or path picks one
EXPORT_FOLDER = None         # folder on disk. None = the config's paths.metahuman_export/<blueprint name>, else
                             # <project>/Saved/MetaHumanExport/<blueprint name>
EXPORT_BODY = True
EXPORT_FACE = True
EXPORT_DNA = True            # the head DNA (<name>.dna), for the MetaHuman DNA add-on in Blender, which runs RigLogic so
                             # the face follows the face board. Needs the MetaHuman Character asset (MetaHuman Creator).
DNA_PROJECT_PATH = ""        # where Unreal may also put the DNA as an asset, if it insists on one; "" = next to the character
EXPORT_CLOTHING = True       # the blueprint's other skeletal meshes (the outfit): the body mesh has no skin where
                             # the outfit covers it, so without them the torso shows as a hole
MORPH_TARGETS = True         # the face's morph targets (needed by the DNA add-on's correctives; slower)
BODY_COMPONENTS = ("Body",)  # blueprint component names, tried in order
FACE_COMPONENTS = ("Face",)
EXPORT_TEXTURES = True       # the textures of the body, face and outfit materials, so Blender shows the real skin
TEXTURE_FORMAT = "png"       # "png" or "tga"
TEXTURE_SKIP = ("_cm1", "_cm2", "_cm3", "_wm1", "_wm2", "_wm3", "wrinkle", "mask", "lut", "noise")
                             # textures whose parameter or asset name holds one of these aren't exported: the
                             # face's wrinkle and region maps, which Blender's materials don't use (4K each)
EXPORT_HAIR = True           # the hair cards of the blueprint's grooms, with their textures (strands can't be exported)
HAIR_SKIP = ("fuzz",)        # groom components left out (name contains one of these, any case)
SHOW_DIALOG = True
FACE_BOARD_SCRIPT = "export_face_board.py"   # its blueprint finder is reused

ASSETS = getattr(unreal, "EditorAssetLibrary", None)
_lines = []


def out(text=""):
    _lines.append(str(text))
    unreal.log(str(text))


def warn(text):
    _lines.append("WARNING: " + str(text))
    unreal.log_warning(str(text))


def attempt(func, default=None):
    try:
        return func()
    except Exception:
        return default


def _full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path))


def asset_path(asset):
    return str(attempt(lambda: asset.get_path_name(), "")).split(".")[0]


# ------------------------------------------------------------------ the blueprint
def face_board_module():
    """export_face_board.py (next to this script), for its blueprint finder and config reading."""
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    path = os.path.join(here, FACE_BOARD_SCRIPT)
    if not here or not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_face_board", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_root(bp_name):
    """<the config's paths.metahuman_export>/<blueprint>, else <project>/Saved/MetaHumanExport/<blueprint>."""
    module = attempt(face_board_module)
    pc, cfg = attempt(module.pipeline_settings, (None, None)) if module is not None else (None, None)
    if cfg:
        return os.path.join(pc.project_path(cfg, "metahuman_export"), bp_name)
    return os.path.join(_full(unreal.Paths.project_saved_dir()), "MetaHumanExport", bp_name)


def find_blueprint(wanted):
    module = attempt(face_board_module)
    if module is not None:
        if wanted is None:
            wanted = attempt(lambda: module.load_settings()[1])
        return module.find_blueprint(wanted)
    # without export_face_board.py: a path, or a blueprint of that name anywhere in /Game
    if wanted and str(wanted).startswith("/"):
        asset = attempt(lambda: unreal.load_asset(str(wanted)))
        return asset, asset_path(asset) if asset else ""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for data in registry.get_assets_by_path("/Game", recursive=True):
        if str(data.asset_name).lower() == str(wanted or "").lower():
            asset = data.get_asset()
            return asset, asset_path(asset)
    return None, ""


def mesh_of(component):
    for name in ("skeletal_mesh_asset", "skeletal_mesh"):
        mesh = attempt(lambda: component.get_editor_property(name))
        if mesh is not None:
            return mesh
    return None


def component_meshes(blueprint):
    """{component name: skeletal mesh} from the blueprint's components."""
    found = {}
    subsystem = attempt(lambda: unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem))
    library = getattr(unreal, "SubobjectDataBlueprintFunctionLibrary", None)
    if subsystem is None or library is None:
        return found
    handles = attempt(lambda: subsystem.k2_gather_subobject_data_for_blueprint(blueprint), []) or []
    for handle in handles:
        data = attempt(lambda: subsystem.k2_find_subobject_data_from_handle(handle))
        if data is None:
            data = attempt(lambda: library.get_data(handle))
        if data is None:
            continue
        component = attempt(lambda: library.get_associated_object(data))     # 5.8; get_object is deprecated
        if component is None and hasattr(library, "get_object"):
            component = attempt(lambda: library.get_object(data))
        if component is None or not isinstance(component, unreal.SkeletalMeshComponent):
            continue
        name = str(attempt(lambda: library.get_variable_name(data), "") or attempt(lambda: component.get_name(), ""))
        mesh = mesh_of(component)
        if mesh is not None:
            found[name] = mesh
    return found


def find_character(blueprint):
    """The MetaHuman Character asset (what MetaHuman Creator edits) behind the blueprint."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    bp_folder = asset_path(blueprint).rsplit("/", 1)[0]
    bp_name = asset_path(blueprint).rsplit("/", 1)[-1]
    wanted = bp_name[3:] if bp_name.startswith("BP_") else bp_name
    found = []
    for data in registry.get_assets_by_path("/Game", recursive=True):
        cls = str(attempt(lambda: data.asset_class_path.asset_name, "") or attempt(lambda: data.asset_class, ""))
        if cls != "MetaHumanCharacter":
            continue
        name, folder = str(data.asset_name), str(data.package_path)
        rank = (0 if name == wanted else 1, 0 if folder == bp_folder or bp_folder.startswith(folder + "/") else 1, folder)
        found.append((rank, data))
    found.sort(key=lambda row: row[0])
    for _, data in found:
        asset = data.get_asset()
        if asset is not None:
            return asset
    return None


def set_any(target, names, value):
    """Set the first of these editor property spellings this engine version has."""
    for name in names:
        try:
            target.set_editor_property(name, value)
            return name
        except Exception:
            continue
    return None


def export_head_dna(character, folder):
    """Write the head DNA into `folder`. Returns (the new .dna files, note)."""
    library = getattr(unreal, "MetaHumanCharacterExportBlueprintLibrary", None)
    params_class = getattr(unreal, "MetaHumanDNAExportParams", None)
    if library is None or params_class is None:
        return [], "this engine has no MetaHumanCharacterExportBlueprintLibrary (MetaHuman Creator plugin off?)"
    def dna_files():
        """{path relative to folder: modified time} of every .dna under the folder (Unreal may make a sub-folder)."""
        found = {}
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".dna"):
                    path = os.path.join(root, f)
                    found[os.path.relpath(path, folder)] = os.path.getmtime(path)
        return found

    before = dna_files()
    started = time.time()
    project = DNA_PROJECT_PATH or asset_path(character).rsplit("/", 1)[0] + "/DNA"

    def attempt_export(project_path):
        params = params_class()
        set_any(params, ("dna_head", "b_dna_head", "export_head", "head"), True)
        set_any(params, ("dna_body", "b_dna_body", "export_body", "body"), False)
        set_any(params, ("overwrite_existing_assets", "b_overwrite_existing_assets"), True)
        set_any(params, ("external_path",), folder)
        if project_path is not None:
            set_any(params, ("project_path",), project_path)
        library.export_dna(character, params)

    errors = []
    for project_path in (None, project):           # first only to disk; then also as an asset, if Unreal needs one
        try:
            attempt_export(project_path)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        new = [f for f, t in dna_files().items() if (f not in before or t > before[f]) and t >= started - 2]
        if new:
            return sorted(new), ("" if project_path is None else f"(Unreal also saved it as an asset in {project_path})")
    return [], "; ".join(errors) or "Unreal wrote no .dna file"


def folder_meshes(blueprint):
    """Fallback: skeletal meshes in the blueprint's folder, by where they sit and what they are called."""
    folder = asset_path(blueprint).rsplit("/", 1)[0]
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    body = face = None
    for data in registry.get_assets_by_path(folder, recursive=True):
        cls = str(attempt(lambda: data.asset_class_path.asset_name, "") or attempt(lambda: data.asset_class, ""))
        if cls != "SkeletalMesh":
            continue
        path, name = str(data.package_path), str(data.asset_name).lower()
        if face is None and ("/face" in path.lower() or "face" in name):
            face = data.get_asset()
        elif body is None and ("/body" in path.lower() or "body" in name):
            body = data.get_asset()
    return body, face


def pick(meshes, names):
    for wanted in names:
        for name, mesh in meshes.items():
            if name.lower() == wanted.lower():
                return mesh
    return None


# ------------------------------------------------------------------ materials, textures and hair cards
def blueprint_components(blueprint):
    """[(component name, component)] for every component of the blueprint."""
    found = []
    subsystem = attempt(lambda: unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem))
    library = getattr(unreal, "SubobjectDataBlueprintFunctionLibrary", None)
    if subsystem is None or library is None:
        return found
    for handle in attempt(lambda: subsystem.k2_gather_subobject_data_for_blueprint(blueprint), []) or []:
        data = attempt(lambda: subsystem.k2_find_subobject_data_from_handle(handle)) or attempt(lambda: library.get_data(handle))
        if data is None:
            continue
        component = attempt(lambda: library.get_associated_object(data))
        if component is None and hasattr(library, "get_object"):
            component = attempt(lambda: library.get_object(data))
        if component is None:
            continue
        name = str(attempt(lambda: library.get_variable_name(data), "") or attempt(lambda: component.get_name(), ""))
        if any(name == n for n, _ in found):
            continue                                  # the same component, reached twice (inherited and own handles)
        found.append((name, component))
    return found


def _param_name(value):
    info = attempt(lambda: value.get_editor_property("parameter_info"))
    return str(attempt(lambda: info.get_editor_property("name"), "") or attempt(lambda: value.get_editor_property("parameter_name"), ""))


def material_parameters(material):
    """({texture parameter: texture}, {vector parameter: [r, g, b, a]}, {scalar parameter: value}) of a material
    or material instance, the instance's own values over its parents'."""
    textures, vectors, scalars = {}, {}, {}
    lib = getattr(unreal, "MaterialEditingLibrary", None)
    chain, current = [], material
    while current is not None and len(chain) < 8:                 # instance -> parent -> ... -> base material
        chain.append(current)
        current = attempt(lambda: current.get_editor_property("parent"))
    for mat in reversed(chain):                                   # parents first, so the instance's values win
        for value in attempt(lambda: mat.get_editor_property("texture_parameter_values"), []) or []:
            tex = attempt(lambda: value.get_editor_property("parameter_value"))
            if tex is not None:
                textures[_param_name(value)] = tex
        for value in attempt(lambda: mat.get_editor_property("vector_parameter_values"), []) or []:
            c = attempt(lambda: value.get_editor_property("parameter_value"))
            if c is not None:
                vectors[_param_name(value)] = [round(float(getattr(c, k)), 5) for k in ("r", "g", "b", "a")]
        for value in attempt(lambda: mat.get_editor_property("scalar_parameter_values"), []) or []:
            v = attempt(lambda: value.get_editor_property("parameter_value"))
            if v is not None:
                scalars[_param_name(value)] = round(float(v), 5)
    if lib is not None:                                           # parameters left at the base material's defaults
        for name in attempt(lambda: lib.get_texture_parameter_names(material), []) or []:
            name = str(name)
            if name not in textures:
                tex = (attempt(lambda: lib.get_material_instance_texture_parameter_value(material, name))
                       or attempt(lambda: lib.get_material_default_texture_parameter_value(chain[-1], name)))
                if tex is not None:
                    textures[name] = tex
        if not textures:
            used = getattr(lib, "get_material_used_textures", None) or getattr(lib, "get_used_textures", None)
            for tex in attempt(lambda: used(material if used.__name__ == "get_material_used_textures" else chain[-1]), []) or []:
                textures[tex.get_name()] = tex
    return textures, vectors, scalars


_exported_textures = {}


def export_texture(texture, folder):
    """Write one texture into `folder` (once per run). Returns its file name relative to the export, or None."""
    key = asset_path(texture)
    if key in _exported_textures:
        return _exported_textures[key]
    if any(word in type(texture).__name__ for word in ("Cube", "RenderTarget", "Volume", "2DArray")):
        _exported_textures[key] = None                # cube maps (eye reflections) and render targets: no image to write
        return None
    os.makedirs(folder, exist_ok=True)
    name = texture.get_name()
    result = None
    for ext, exporter in ((TEXTURE_FORMAT, None), ("tga", "TextureExporterTGA")):
        path = os.path.join(folder, f"{name}.{ext}")
        task = unreal.AssetExportTask()
        task.set_editor_property("object", texture)
        task.set_editor_property("filename", path)
        task.set_editor_property("automated", True)
        task.set_editor_property("prompt", False)
        task.set_editor_property("replace_identical", True)
        if exporter and hasattr(unreal, exporter):
            task.set_editor_property("exporter", getattr(unreal, exporter)())
        attempt(lambda: unreal.Exporter.run_asset_export_task(task))
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            result = os.path.basename(folder) + "/" + os.path.basename(path)
            break
    _exported_textures[key] = result
    return result


def mesh_materials(mesh, component=None):
    """[(slot name, material)] of a skeletal or static mesh, with the component's overrides."""
    slots = attempt(lambda: mesh.get_editor_property("materials")) or attempt(lambda: mesh.get_editor_property("static_materials")) or []
    overrides = list(attempt(lambda: component.get_editor_property("override_materials"), []) or []) if component else []
    found = []
    for i, slot in enumerate(slots):
        material = overrides[i] if i < len(overrides) and overrides[i] is not None else attempt(lambda: slot.get_editor_property("material_interface"))
        name = str(attempt(lambda: slot.get_editor_property("material_slot_name"), "") or "")
        found.append((name, material))
    return found


def describe_material(slot, material, folder):
    """A material slot as written to metahuman_export.json, its textures exported."""
    entry = {"slot": slot, "material": material.get_name() if material else "", "path": asset_path(material) if material else ""}
    if material is None:
        return entry
    parent, current = None, material
    while current is not None:
        parent, current = current, attempt(lambda: current.get_editor_property("parent"))
    entry["base_material"] = parent.get_name() if parent is not None else ""
    textures, vectors, scalars = material_parameters(material)
    entry["textures"], entry["skipped_textures"] = {}, []
    for param, tex in sorted(textures.items()):
        label = (param + " " + tex.get_name()).lower()
        if any(word in label for word in TEXTURE_SKIP):
            entry["skipped_textures"].append(param)
            continue
        written = export_texture(tex, os.path.join(folder, "textures"))
        if written:
            entry["textures"][param] = written
    entry["vectors"], entry["scalars"] = vectors, scalars
    return entry


def export_materials(parts, components, folder, record):
    """The material slots of the exported meshes, with their textures, into record['materials'][part]."""
    by_mesh = {}
    for name, component in components:
        mesh = mesh_of(component) if isinstance(component, unreal.SkeletalMeshComponent) else None
        if mesh is not None:
            by_mesh.setdefault(asset_path(mesh), component)
    record["materials"] = {}
    total = 0
    for part, mesh, wanted in parts:
        if not wanted or mesh is None:
            continue
        slots = [describe_material(slot, mat, folder) for slot, mat in mesh_materials(mesh, by_mesh.get(asset_path(mesh)))]
        record["materials"][part] = slots
        count = sum(len(e.get("textures", {})) for e in slots)
        total += count
        out(f"materials of {part}: {len(slots)} slots, {count} textures"
            + (" - " + ", ".join(f"{e['material']} ({len(e.get('textures', {}))})" for e in slots[:8]) if slots else ""))
    return total


def _texture_list(value):
    """Textures held by a hair cards description's texture struct (field names differ between engine versions)."""
    found = []
    if value is None:
        return found
    listed = attempt(lambda: value.get_editor_property("textures"))
    if listed is not None:
        found += [t for t in list(listed) if t is not None]
    for name in ("depth_texture", "coverage_texture", "tangent_texture", "attribute_texture",
                 "auxilary_data_texture", "material_texture"):
        tex = attempt(lambda: value.get_editor_property(name))
        if tex is not None:
            found.append(tex)
    return found


def export_hair(components, folder, record):
    """The card meshes (lowest LOD of each hair group) and their textures, of every groom component."""
    record["hair"] = []
    grooms = [(n, c) for n, c in components if "groom" in type(c).__name__.lower()
              and not any(w in n.lower() for w in HAIR_SKIP)]
    if not grooms:
        out("hair: the blueprint has no groom components")
        return 0, []
    problems = []
    hair_folder = os.path.join(folder, "hair")
    for name, component in grooms:
        groom = attempt(lambda: component.get_editor_property("groom_asset"))
        if groom is None:
            continue                                  # an empty slot (no beard, no mustache)
        materials = {}
        for entry in attempt(lambda: groom.get_editor_property("hair_groups_materials"), []) or []:
            mat = attempt(lambda: entry.get_editor_property("material"))
            slot = str(attempt(lambda: entry.get_editor_property("slot_name"), "") or "")
            if mat is not None:
                materials[slot] = mat
        best = {}
        for card in attempt(lambda: groom.get_editor_property("hair_groups_cards"), []) or []:
            mesh = attempt(lambda: card.get_editor_property("imported_mesh"))
            if mesh is None:
                continue
            group = int(attempt(lambda: card.get_editor_property("group_index"), 0) or 0)
            lod = int(attempt(lambda: card.get_editor_property("lod_index"), 0) or 0)
            if group not in best or lod < best[group][0]:
                best[group] = (lod, mesh, card)
        if not best:
            problems.append(f"{name} ({groom.get_name()}): strands only, no hair cards to export")
            continue
        for group, (lod, mesh, card) in sorted(best.items()):
            os.makedirs(hair_folder, exist_ok=True)
            path = os.path.join(hair_folder, f"{name}_g{group}_lod{lod}.fbx")
            task = unreal.AssetExportTask()
            task.set_editor_property("object", mesh)
            task.set_editor_property("filename", path)
            task.set_editor_property("automated", True)
            task.set_editor_property("prompt", False)
            task.set_editor_property("replace_identical", True)
            exporter = attempt(lambda: unreal.StaticMeshExporterFBX())
            if exporter is not None:
                task.set_editor_property("exporter", exporter)
            options = unreal.FbxExportOption()
            attempt(lambda: options.set_editor_property("level_of_detail", False))
            attempt(lambda: options.set_editor_property("collision", False))
            task.set_editor_property("options", options)
            attempt(lambda: unreal.Exporter.run_asset_export_task(task))
            if not os.path.isfile(path):
                problems.append(f"{name} group {group}: the card mesh {asset_path(mesh)} wasn't written")
                continue
            textures = {}
            for tex in _texture_list(attempt(lambda: card.get_editor_property("textures"))):
                written = export_texture(tex, hair_folder)
                if written:
                    textures[tex.get_name()] = written
            slot = str(attempt(lambda: card.get_editor_property("material_slot_name"), "") or "")
            material = materials.get(slot) or (next(iter(materials.values())) if materials else None)
            _, vectors, scalars = material_parameters(material) if material is not None else ({}, {}, {})
            where = attempt(lambda: component.get_editor_property("relative_location"))
            record["hair"].append({
                "component": name, "groom": asset_path(groom), "group": group, "lod": lod,
                "file": "hair/" + os.path.basename(path), "mesh": asset_path(mesh), "textures": textures,
                "material": material.get_name() if material is not None else "", "vectors": vectors, "scalars": scalars,
                "attach_socket": str(attempt(lambda: component.get_editor_property("attach_socket_name"), "") or ""),
                "relative_location": [float(where.x), float(where.y), float(where.z)] if where is not None else None})
            out(f"hair: {name} group {group} (LOD {lod}) -> {os.path.basename(path)}, {len(textures)} textures")
    for p in problems:
        warn("hair: " + p)
    return len(record["hair"]), problems


# ------------------------------------------------------------------ export
def export_mesh(mesh, path):
    """Write one skeletal mesh (with its skeleton, no animation) as FBX. Returns (ok, errors)."""
    options = unreal.FbxExportOption()
    for name, value in (("ascii", False), ("level_of_detail", False), ("collision", False),
                        ("export_morph_targets", MORPH_TARGETS), ("export_preview_mesh", False),
                        ("map_skeletal_motion_to_root", False), ("vertex_color", True)):
        attempt(lambda: options.set_editor_property(name, value))
    task = unreal.AssetExportTask()
    task.set_editor_property("object", mesh)
    task.set_editor_property("filename", path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("options", options)
    exporter = attempt(lambda: unreal.SkeletalMeshExporterFBX())
    if exporter is not None:
        task.set_editor_property("exporter", exporter)
    ok = bool(unreal.Exporter.run_asset_export_task(task))
    errors = [str(e) for e in (attempt(lambda: task.get_editor_property("errors"), []) or [])]
    return ok and os.path.isfile(path), errors


def export_metahuman():
    """Returns (ok, message)."""
    started = time.time()
    _exported_textures.clear()
    blueprint, note = find_blueprint(METAHUMAN_BLUEPRINT)
    if blueprint is None:
        message = (f"No MetaHuman blueprint found" + (f" named {METAHUMAN_BLUEPRINT}" if METAHUMAN_BLUEPRINT else "")
                   + ". Set METAHUMAN_BLUEPRINT at the top of this script to its name or path.")
        warn(message)
        return False, message
    bp_name = asset_path(blueprint).rsplit("/", 1)[-1]
    out(f"MetaHuman: {note or asset_path(blueprint)}")

    meshes = component_meshes(blueprint)
    if meshes:
        out("Components with a skeletal mesh: " + ", ".join(f"{n} ({asset_path(m)})" for n, m in meshes.items()))
    body, face = pick(meshes, BODY_COMPONENTS), pick(meshes, FACE_COMPONENTS)
    if body is None or face is None:
        fb_body, fb_face = folder_meshes(blueprint)
        if body is None and fb_body is not None:
            body = fb_body
            out(f"Body mesh taken from the blueprint's folder: {asset_path(body)}")
        if face is None and fb_face is not None:
            face = fb_face
            out(f"Face mesh taken from the blueprint's folder: {asset_path(face)}")

    folder = EXPORT_FOLDER or export_root(bp_name)
    folder = os.path.normpath(folder)
    os.makedirs(folder, exist_ok=True)
    out(f"Writing to {folder}")

    record = {"blueprint": asset_path(blueprint), "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
              "unreal_version": str(attempt(lambda: unreal.SystemLibrary.get_engine_version(), "")), "files": {}}
    parts = [("body", body, EXPORT_BODY), ("face", face, EXPORT_FACE)]
    others = [(n, m) for n, m in meshes.items() if m is not None and m is not body and m is not face]
    for i, (name, mesh) in enumerate(others):
        parts.append(("clothing" if len(others) == 1 else f"clothing_{i + 1}", mesh, EXPORT_CLOTHING))
    results = []
    for part, mesh, wanted in parts:
        if not wanted:
            continue
        if mesh is None:
            warn(f"{part}: no skeletal mesh found on {bp_name} (components tried: "
                 f"{', '.join(BODY_COMPONENTS if part == 'body' else FACE_COMPONENTS)})")
            results.append(False)
            continue
        path = os.path.join(folder, f"{bp_name}_{part}.fbx")
        ok, errors = export_mesh(mesh, path)
        skeleton = attempt(lambda: mesh.get_editor_property("skeleton"))
        if ok:
            out(f"{part}: {asset_path(mesh)} -> {os.path.basename(path)} "
                f"({os.path.getsize(path) / 1e6:.1f} MB)")
            record["files"][part] = {"file": os.path.basename(path), "mesh": asset_path(mesh),
                                     "skeleton": asset_path(skeleton) if skeleton else ""}
        else:
            warn(f"{part}: export of {asset_path(mesh)} FAILED" + (": " + "; ".join(errors) if errors else ""))
        results.append(ok)

    components = blueprint_components(blueprint) if (EXPORT_TEXTURES or EXPORT_HAIR) else []
    if EXPORT_TEXTURES:
        try:
            count = export_materials(parts, components, folder, record)
            out(f"textures: {count} written to {os.path.join(folder, 'textures')}")
        except Exception as err:
            warn(f"textures: stopped - {type(err).__name__}: {err}")
    if EXPORT_HAIR:
        try:
            count, problems = export_hair(components, folder, record)
            if count:
                out(f"hair: {count} card mesh(es) written to {os.path.join(folder, 'hair')}")
            elif components:
                warn("hair: nothing exported" + (f" ({'; '.join(problems[:3])})" if problems else ""))
        except Exception as err:
            warn(f"hair: stopped - {type(err).__name__}: {err}")

    if EXPORT_DNA:
        character = find_character(blueprint)
        if character is None:
            warn("head DNA: no MetaHuman Character asset found in the project (the asset MetaHuman Creator opens). "
                 "Export the DNA by hand, or set EXPORT_DNA = False.")
            results.append(False)
        else:
            files, note = export_head_dna(character, folder)
            if files:
                out(f"head DNA: {asset_path(character)} -> {', '.join(files)} {note}".rstrip())
                record["files"]["head_dna"] = {"file": files[0], "character": asset_path(character)}
            else:
                warn(f"head DNA: export of {asset_path(character)} FAILED: {note}")
            results.append(bool(files))

    with open(os.path.join(folder, "metahuman_export.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    with open(os.path.join(folder, "export_metahuman_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")
    ok = bool(results) and all(results)
    message = (f"MetaHuman {bp_name}: " + ("exported" if ok else "NOT fully exported (see the Output Log)")
               + f" to {folder} in {time.time() - started:.0f} s.\nNext: in an empty Blender file, run "
               "build_metahuman_rig.py (it finds this export by itself).")
    out(message)
    return ok, message


def _main():
    ok, message = export_metahuman()
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        attempt(lambda: unreal.EditorDialog.show_message("Export MetaHuman", message, unreal.AppMsgType.OK))
    return ok


if __name__ != "export_metahuman":
    _main()
