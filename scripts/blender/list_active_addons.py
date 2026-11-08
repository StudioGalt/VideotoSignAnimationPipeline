"""
List every add-on / extension that is currently enabled in this Blender session.

Run from the Text Editor. The list is printed to the system console
(Window > Toggle System Console on Windows) and also written to a Text Editor block
called "active_addons.txt" so it can be copied easily.

The "module" column is the exact name a script can check, e.g.
    addon_utils.check("<module>")[1]      -> True when enabled
"""

import addon_utils
import bpy

TEXT_NAME = "active_addons.txt"


def addon_info(module_name):
    """Display name, version and kind for an enabled add-on module name."""
    mod = next((m for m in addon_utils.modules(refresh=False) if m.__name__ == module_name), None)
    name, version = module_name, ""
    if mod is not None:
        try:
            info = addon_utils.module_bl_info(mod)
            name = info.get("name") or module_name
            v = info.get("version")
            if v:
                version = ".".join(str(x) for x in v) if isinstance(v, (tuple, list)) else str(v)
        except Exception:
            pass
    kind = "extension" if module_name.startswith("bl_ext.") else "add-on"
    return name, version, kind


def main():
    enabled = sorted(bpy.context.preferences.addons.keys(), key=str.lower)
    rows = [(module,) + addon_info(module) for module in enabled]

    lines = [f"Blender {bpy.app.version_string} - {len(rows)} enabled add-ons / extensions", ""]
    width = max((len(r[1]) for r in rows), default=10)
    for module, name, version, kind in sorted(rows, key=lambda r: r[1].lower()):
        lines.append(f"{name:<{width}}  {version:<10} {kind:<9}  module: {module}")

    # What the procedural fixes rely on
    lines += ["", "Used by the procedural fixes:"]
    anim_layers = hasattr(bpy.types.Object, "Anim_Layers") and hasattr(bpy.ops.anim, "add_anim_layer")
    anim_layers_mod = next((m for m in enabled if "anim" in m.lower() and "layer" in m.lower()), None)
    lines.append(f"  Animation Layers : {'FOUND' if anim_layers else 'NOT FOUND'}"
                 + (f"  (module: {anim_layers_mod})" if anim_layers_mod else ""))
    rigify_mod = next((m for m in enabled if m.split(".")[-1] == "rigify"), None)
    lines.append(f"  Rigify           : {'FOUND' if rigify_mod else 'NOT FOUND'}"
                 + (f"  (module: {rigify_mod})" if rigify_mod else ""))

    report = "\n".join(lines)
    print("\n" + report + "\n")

    text = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
    text.clear()
    text.write(report + "\n")
    print(f"(Also written to the Text Editor block '{TEXT_NAME}')")


if __name__ == "__main__":
    main()
