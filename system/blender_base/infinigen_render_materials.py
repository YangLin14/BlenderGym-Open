"""
Given a python file (indicated inthe commandline path), render the material output.
"""

import bpy
import random
import json
import os
import sys
from sys import platform


# def get_material_from_code(code_fpath):
#     assert os.path.exists(code_fpath)
#     import pdb; pdb.set_trace()
#     with open(code_fpath, "r") as f:
#         code = f.read()
#     exec(code)
#     return material 


if __name__ == "__main__":

    code_fpath = sys.argv[6]  # TODO: allow a folder to be given, each with a possible guess.
    rendering_fpath = sys.argv[7] # rendering


    bpy.context.scene.render.engine = "CYCLES"
    # setting up Rendering settings
    if platform == "linux" or platform == "linux2":
        # linux
        bpy.context.preferences.addons[
            "cycles"
        ].preferences.compute_device_type = "CUDA"
        bpy.context.scene.cycles.device = "GPU"   
        
    elif platform == "darwin":
        # OS X
        bpy.context.preferences.addons[
            "cycles"
        ].preferences.compute_device_type = "METAL"
        
    elif platform == "win32":
        # Windows...
        raise NotImplemented("Not supported")
    

    bpy.context.preferences.addons["cycles"].preferences.get_devices()
    print(bpy.context.preferences.addons["cycles"].preferences.compute_device_type)
    for d in bpy.context.preferences.addons["cycles"].preferences.devices:
        d["use"] = 1 # Using all devices, include GPU and CPU
        print(d["name"], d["use"])

    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 512

    # Find a mesh that can be assigned material
    material_obj = None
    for obj in bpy.data.objects:
        if obj.type == "MESH":
             material_obj = obj
             break
    del obj
    assert material_obj is not None, "object doesn't exist"

     # clear preexisting materials on the selected mesh
    material_obj.data.materials.clear()

    for mat in bpy.data.materials:
        bpy.data.materials.remove(mat, do_unlink=True)
    assert len(bpy.data.materials) == 0

    # creating the material and assigning it to the sphere
    with open(code_fpath, "r") as f:
        code = f.read()
    try:
        exec(code)
    except:
        raise ValueError
    
    # render, and save.
    bpy.context.scene.render.image_settings.file_format = 'PNG'
    bpy.context.scene.render.filepath = rendering_fpath
    bpy.ops.render.render(write_still=True)


    # print( f"Poppping material at index {material_index}")
    # # save to disk
    # material_obj.data.materials.pop(index=material_index)



