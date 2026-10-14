"""Feasibility spike: Zero (the yellow plush O) built, furred and animated in Blender, rendered with EEVEE.
Throwaway. Run: Blender -b -P zero_spike.py -- OUTDIR [frames]"""
import math, sys
import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/zero-spike"
MODE = argv[1] if len(argv) > 1 else "idle"
FRAMES = {"idle": 96, "happy": 60, "talk": 72, "walkup": 90}.get(MODE, 96)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
import addon_utils
addon_utils.enable("cycles", default_set=True)
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "METAL"; prefs.get_devices()
# Background kernel specialization crashed Blender 5.2.1 while saving Metal binaries (Oct 8); keep it off.
if hasattr(prefs, "kernel_optimization_level"): prefs.kernel_optimization_level = "OFF"
for d in prefs.devices: d.use = True
scene.cycles.device = "GPU"
scene.cycles.samples = 48; scene.cycles.use_denoising = True
scene.cycles.max_bounces = 6
scene.render.resolution_x = scene.render.resolution_y = 560
scene.render.fps = 24
scene.frame_start, scene.frame_end = 1, FRAMES
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = -0.6

def mat(name, color, rough=0.5, spec=0.5, coat=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    if "Coat Weight" in b.inputs: b.inputs["Coat Weight"].default_value = coat
    return m

def srgb(h):
    h = h.lstrip("#"); c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92 for x in c)

YELLOW = srgb("#FFC400")
FUR_HEX = tuple(argv[3].split(",")) if len(argv) > 3 else ("#FAC800", "#FFE15A")
root = bpy.data.objects.new("Rig", None); scene.collection.objects.link(root)

bpy.ops.mesh.primitive_torus_add(major_radius=0.66, minor_radius=0.34, major_segments=96, minor_segments=40,
                                 location=(0, 0, 1.0), rotation=(math.pi / 2, 0, 0))
body = bpy.context.object; body.name = "Body"
body.scale = (0.92, 1.25, 1.0)
body.modifiers.new("Smooth", "SUBSURF").levels = 2
felt = mat("Felt", YELLOW, rough=0.9)
fb = felt.node_tree.nodes["Principled BSDF"]
if "Sheen Weight" in fb.inputs:
    fb.inputs["Sheen Weight"].default_value = 1.0; fb.inputs["Sheen Roughness"].default_value = 0.35
    fb.inputs["Sheen Tint"].default_value = (1, 0.93, 0.7, 1)
if "Subsurface Weight" in fb.inputs:
    fb.inputs["Subsurface Weight"].default_value = 0.25; fb.inputs["Subsurface Radius"].default_value = (1, 0.6, 0.2)
body.data.materials.append(felt)
body.parent = root

ps_mod = body.modifiers.new("Fur", "PARTICLE_SYSTEM")
ps = ps_mod.particle_system.settings
ps.type = "HAIR"; ps.count = 30000; ps.hair_length = 0.085
ps.child_type = "INTERPOLATED"; ps.child_percent = 8; ps.rendered_child_count = 14
ps.clump_factor = 0.1; ps.kink = "NO"; ps.roughness_1 = 0.02
ps.child_radius = 0.06; ps.roughness_2 = 0.08; ps.roughness_endpoint = 0.02
ps.root_radius = 1.0; ps.tip_radius = 0.0; ps.radius_scale = 0.011
ps.use_advanced_hair = True
fur = bpy.data.materials.new("Fur"); fur.use_nodes = True
nt = fur.node_tree; hb = nt.nodes["Principled BSDF"]   # a plain surface shader keeps the true color on strands
hb.inputs["Roughness"].default_value = 0.55
if "Sheen Weight" in hb.inputs:
    hb.inputs["Sheen Weight"].default_value = 0.8; hb.inputs["Sheen Tint"].default_value = (1, 0.95, 0.8, 1)
nt = fur.node_tree; info = nt.nodes.new("ShaderNodeHairInfo"); ramp = nt.nodes.new("ShaderNodeValToRGB")
FUR_ROOT, FUR_TIP = srgb(FUR_HEX[0]), srgb(FUR_HEX[1])   # warmer than the body: the hair shader pulls yellow toward green
ramp.color_ramp.elements[0].color = (*FUR_ROOT, 1)
ramp.color_ramp.elements[1].color = (*FUR_TIP, 1)
nt.links.new(info.outputs["Intercept"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], hb.inputs["Base Color"])
body.data.materials.append(fur); ps.material = 2

eye_mat = mat("Bead", (0.01, 0.01, 0.012), rough=0.06, coat=1.0)
eyes = []
for x in (-0.26, 0.26):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.11, location=(x, -0.48, 1.80), segments=48, ring_count=24)
    e = bpy.context.object; e.scale = (1, 0.7, 1.12); e.data.materials.append(eye_mat)
    bpy.ops.object.shade_smooth(); e.parent = root; eyes.append(e)
arcs = []
for x in (-0.26, 0.26):
    bpy.ops.curve.primitive_bezier_curve_add(location=(x, -0.55, 1.79))
    a = bpy.context.object
    ap = a.data.splines[0].bezier_points
    ap[0].co, ap[1].co = Vector((-0.075, 0, -0.025)), Vector((0.075, 0, -0.025))
    ap[0].handle_right, ap[1].handle_left = Vector((-0.045, 0, 0.06)), Vector((0.045, 0, 0.06))
    ap[0].handle_left, ap[1].handle_right = Vector((-0.1, 0, -0.06)), Vector((0.1, 0, -0.06))
    a.data.bevel_depth = 0.017; a.data.bevel_resolution = 4
    a.data.materials.append(eye_mat); a.parent = root; a.hide_render = True
    arcs.append(a)
bpy.ops.curve.primitive_bezier_curve_add(location=(0, -0.52, 1.6))
smile = bpy.context.object
pts = smile.data.splines[0].bezier_points
pts[0].co, pts[1].co = Vector((-0.08, 0, 0.0)), Vector((0.08, 0, 0.0))
pts[0].handle_right, pts[1].handle_left = Vector((-0.03, 0, -0.05)), Vector((0.03, 0, -0.05))
pts[0].handle_left, pts[1].handle_right = Vector((-0.1, 0, 0.04)), Vector((0.1, 0, 0.04))
smile.data.bevel_depth = 0.014; smile.data.materials.append(mat("Mouth", (0.05, 0.02, 0.02), rough=0.4))
smile.parent = root
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.07, location=(0, -0.5, 1.6), segments=32, ring_count=16)
mouth = bpy.context.object; mouth.data.materials.append(mat("MouthIn", (0.12, 0.02, 0.03), rough=0.5))
bpy.ops.object.shade_smooth(); mouth.parent = root; mouth.scale = (1, 0.4, 0.001)

glass = mat("Frames", (0.02, 0.02, 0.02), rough=0.2)
for x in (-0.26, 0.26):
    bpy.ops.mesh.primitive_torus_add(major_radius=0.19, minor_radius=0.016, location=(x, -0.6, 1.80),
                                     rotation=(math.pi / 2, 0, 0), major_segments=64, minor_segments=12)
    g = bpy.context.object; g.data.materials.append(glass); bpy.ops.object.shade_smooth(); g.parent = root
bpy.ops.mesh.primitive_cylinder_add(radius=0.014, depth=0.16, location=(0, -0.61, 1.86), rotation=(0, math.pi / 2, 0))
br = bpy.context.object; br.data.materials.append(glass); br.parent = root

import bmesh
prof = [(y, 0.0) for y in (-30, -10, 0, 18)] + [(18 + 6 * math.sin(a), 6 - 6 * math.cos(a)) for a in [i * math.pi / 24 for i in range(1, 13)]] + [(24, 20), (24, 40)]
bm = bmesh.new()
rows = [[bm.verts.new((x, y, z)) for (y, z) in prof] for x in (-40, 40)]
for i in range(len(prof) - 1):
    bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
me = bpy.data.meshes.new("Sweep"); bm.to_mesh(me); bm.free()
floor = bpy.data.objects.new("Sweep", me); scene.collection.objects.link(floor)
for poly in me.polygons: poly.use_smooth = True
sweep = mat("Sweep", (0.92, 0.92, 0.92), rough=0.95)
sb = sweep.node_tree.nodes["Principled BSDF"]
sb.inputs["Emission Color"].default_value = (1, 1, 1, 1); sb.inputs["Emission Strength"].default_value = 0.85
floor.data.materials.append(sweep)
world = bpy.data.worlds.new("W"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.5
for name, loc, energy, size in [("Key", (-3.5, -4.5, 5.5), 650, 5), ("Fill", (4, -4, 2.5), 180, 6), ("Rim", (1.5, 3, 4), 350, 3), ("Sweep", (0, -2, 7), 300, 8)]:
    L = bpy.data.lights.new(name, "AREA"); L.energy = energy; L.size = size
    o = bpy.data.objects.new(name, L); o.location = loc; scene.collection.objects.link(o)
    o.rotation_euler = (Vector((0, 0, 1.0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
cam = bpy.data.cameras.new("Cam"); cam.lens = 85
co = bpy.data.objects.new("Cam", cam); co.location = (0, -7.4, 1.2); co.rotation_euler = (math.radians(89), 0, 0)
scene.collection.objects.link(co); scene.camera = co

def key(obj, *paths, f):
    for pth in paths: obj.keyframe_insert(pth, frame=f)

for f in range(1, FRAMES + 1):
    t = (f - 1) / 24
    breath = math.sin(2 * math.pi * t / 2.2)
    sx, sz, lift, y = 1 - 0.012 * breath, 1 + 0.018 * breath, 0.0, 0.0
    sway = math.sin(2 * math.pi * t / 4.0)
    eyes_open, glance, talk = 1.0, 0.0, 0.0
    if MODE == "idle":
        if 1.2 <= t < 1.33: eyes_open = 0.1
        def look(t0, t1, amount):
            if t0 <= t < t1:
                return amount * min(1, (t - t0) / 0.12, (t1 - t) / 0.12)
            return 0.0
        glance = look(0.4, 1.0, 0.03) + look(2.0, 2.9, -0.035) + look(3.3, 3.8, 0.02)
    elif MODE == "happy":
        if t >= 0.25: eyes_open = 0.5
    elif MODE == "talk":
        talk = max(0.0, math.sin(2 * math.pi * t * 3.1) * 0.6 + math.sin(2 * math.pi * t * 1.7) * 0.4) if 0.2 < t < 2.7 else 0.0
        sz *= 1 + 0.02 * talk
        if 1.6 <= t < 1.72: eyes_open = 0.1
    elif MODE == "walkup":
        u = min(1.0, t / 2.4)
        e = 1 - (1 - u) ** 2.2
        y = 13.0 * (1 - e)
        hop_u = (u * 3) % 1 if u < 1 else 0
        if u < 1:
            lift = 0.55 * math.sin(math.pi * hop_u)
            land = max(0.0, 1 - min(hop_u, 1 - hop_u) / 0.15)
            sx, sz = (1 + 0.09 * land, 1 - 0.11 * land) if land > 0 else (0.95, 1.06)
        else:
            w = t - 2.4; k = math.exp(-7 * w) * math.sin(w * 26); sx, sz = 1 + 0.05 * k, 1 - 0.06 * k
        if 2.9 <= t < 3.02: eyes_open = 0.1
    root.scale = (sx, sx, sz); root.location = (0, y, lift)
    root.rotation_euler = (0, 0, 0)
    key(root, "scale", "location", "rotation_euler", f=f)
    closed = eyes_open < 0.9
    for i, (e, a) in enumerate(zip(eyes, arcs)):
        e.location.x = (-0.26 if i == 0 else 0.26) + glance
        e.hide_render = closed; a.hide_render = not closed
        key(e, "location", "hide_render", f=f); key(a, "hide_render", f=f)
    mouth.scale = (1, 0.4, max(0.001, 0.75 * talk)); key(mouth, "scale", f=f)
    smile.hide_render = talk > 0.15; key(smile, "hide_render", f=f)

scene.render.image_settings.file_format = "PNG"
STILLS = [int(x) for x in argv[2].split(",")] if len(argv) > 2 else []
if STILLS:
    for f in STILLS:
        scene.frame_set(f); scene.render.filepath = f"{OUT}/still_{f:04d}.png"; bpy.ops.render.render(write_still=True)
else:
    scene.render.filepath = OUT + "/f_"
    scene.render.use_overwrite = False
    bpy.ops.render.render(animation=True)
print("rendered", FRAMES, "frames to", OUT)
