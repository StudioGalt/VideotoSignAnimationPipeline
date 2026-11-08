import bpy
from mathutils import Vector

# Lights for looking at the MetaHuman in the viewport's Rendered shading: a key, a fill and a rim light, and a
# soft grey world. They are sun lights, so they light the character the same way whatever its size or where it
# stands. Run it in any file (build_metahuman_rig.py runs it in the character file, and filed takes keep them).
# Running it again only updates the lights; it doesn't add more.

COLLECTION = "MH_lights"
# name, where the light comes from (towards the character's front, its left, up), strength, colour
LIGHTS = [
    ("MH_key_light", (1.0, 0.8, 1.0), 3.0, (1.0, 0.96, 0.9)),
    ("MH_fill_light", (1.0, -1.0, 0.4), 1.2, (0.88, 0.94, 1.0)),
    ("MH_rim_light", (-1.0, 0.3, 1.2), 2.0, (1.0, 1.0, 1.0)),
]
WORLD_COLOR = (0.35, 0.35, 0.37)
WORLD_STRENGTH = 0.5


def character_axes():
    """(front, left, up) in world space: from the MetaHuman rig's shoulders, else facing -Y."""
    up = Vector((0, 0, 1))
    rig = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"), None)
    if rig is not None:
        bones = rig.data.bones
        for left, right in (("upperarm_l", "upperarm_r"), ("thigh_l", "thigh_r")):
            if left in bones and right in bones:
                to_left = (rig.matrix_world @ bones[left].head_local) - (rig.matrix_world @ bones[right].head_local)
                to_left.z = 0.0
                if to_left.length > 1e-9:
                    to_left.normalize()
                    return up.cross(-to_left).normalized(), to_left, up     # facing -Y with its left at +X: front = -Y
    return Vector((0, -1, 0)), Vector((1, 0, 0)), up


def add_lights():
    scene = bpy.context.scene
    coll = bpy.data.collections.get(COLLECTION) or bpy.data.collections.new(COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    front, left, up = character_axes()
    for name, (f, l, u), strength, color in LIGHTS:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "LIGHT":
            data = bpy.data.lights.new(name, "SUN")
            obj = bpy.data.objects.new(name, data)
            coll.objects.link(obj)
        obj.data.energy = strength
        obj.data.color = color
        obj.data.angle = 0.2                       # slightly soft shadows
        direction = (front * f + left * l + up * u).normalized()
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(direction)   # a sun shines down its -Z
    world = scene.world or bpy.data.worlds.new("MH_world")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    background = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if background is not None:
        background.inputs["Color"].default_value = (*WORLD_COLOR, 1.0)
        background.inputs["Strength"].default_value = WORLD_STRENGTH
    world.color = WORLD_COLOR
    print(f"  lights: {', '.join(n for n, *_ in LIGHTS)} in '{COLLECTION}', world grey at {WORLD_STRENGTH:g} "
          "(Viewport Shading > Rendered to see them)")


add_lights()
