# blender packages
import logging
# built-in modules
import sys
from itertools import product
from pathlib import Path

import bpy
# third-party packages
import numpy as np
from bpy.types import (
    Scene, Material, Object
)


def init_scene():
    """
    Initialize a scene with the basic rendering configurations.
    """
    # the bpy.context module is usually read-only, so we access the current scene through bpy.data
    scene_name: str = bpy.context.scene.name
    scene: Scene = bpy.data.scenes[scene_name]
    scene.render.engine = 'BLENDER_EEVEE'
    # scene.render.engine = 'CYCLES'
    # output image settings
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.film_transparent = True  # transparent background
    # remove the default cube and lights created by blender
    for obj in bpy.data.objects:
        if obj.name != 'Camera':
            logging.info(f'remove object {obj.name} from the scene')
            bpy.data.objects.remove(obj)


def create_materials():
    """
    Create materials for rendering the input point cloud / output mesh
    """
    params = [
        {'name': 'mesh1', 'color': (0.8, 0.2, 0.2, 1.0), 'transparent': False},
        {'name': 'mesh2', 'color': (1, 0.8, 0.1, 1.0), 'transparent': False},
        {'name': 'ibs', 'color': (0.794, 0.289, 0.8, 1.0), 'transparent': False},
        {'name': 'PID1', 'color': (0.3, 0.8, 0.3, 1.0), 'transparent': False},
        {'name': 'PID2', 'color': (0.165, 0.564, 0.921, 1.0), 'transparent': False}
    ]
    global protected_material_names
    protected_material_names = [param['name'] for param in params]
    roughness = 0.5
    for param in params:
        # create a new material and enable nodes
        bpy.data.materials.new(name=param['name'])
        material: Material = bpy.data.materials[param['name']]
        material.use_nodes = True

        nodes: bpy_prop_collection = material.node_tree.nodes
        links: bpy_prop_collection = material.node_tree.links
        # remove the default Principle BSDF node in the material's node tree
        for node in nodes:
            if node.type != 'OUTPUT_MATERIAL':
                nodes.remove(node)
        # add a Diffuse BSDF node
        BSDF_node = nodes.new('ShaderNodeBsdfDiffuse')
        BSDF_node.inputs['Color'].default_value = param['color']
        BSDF_node.inputs['Roughness'].default_value = roughness
        output_node: ShaderNodeOutputMaterial = nodes['Material Output']
        if param['transparent']:
            # for a transparent material, create a Mix Shader node and enable color
            # blending
            transparent_node = nodes.new('ShaderNodeBsdfTransparent')
            mix_node = nodes.new('ShaderNodeMixShader')
            mix_node.inputs['Fac'].default_value = 0.5

            # here we have to use index instead of key to access the 'Shader' input
            # of a Mix Shader node, because there are two input slots with the same
            # name 'Shader' and we need to use both of them
            links.new(BSDF_node.outputs['BSDF'], mix_node.inputs[1])
            links.new(transparent_node.outputs['BSDF'], mix_node.inputs[2])
            links.new(mix_node.outputs['Shader'], output_node.inputs['Surface'])

            material.blend_method = 'BLEND'
            material.shadow_method = 'CLIP'
        else:
            # for a non-transparent material, link the Diffuse BSDF node's output
            # with the output node's input
            links.new(BSDF_node.outputs['BSDF'], output_node.inputs['Surface'])

        logging.info('Diffuse BSDF material {} has been created'.format(param['name']))


def init_camera():
    """
    Set the camera's position
    """
    camera_obj: Object = bpy.data.objects['Camera']
    # the location is obtained through GUI
    camera_obj.location = (0.7359, -0.6926, 0.4958)


def init_lights(scale_factor: float = 1):
    """
    Set lights for rendering.
    By default, this function will place
      - two sun lights above the object
      - one point light below the object
    The object is assumed to be normalized, i.e. it can be enclosed by a unit cube
    centered at (0, 0, 0).
    To render larger objects, pass the `scale_factor` parameter explicitly to scale
    the locations of lights.
    """
    # all parameters are obtained through blender GUI
    # the unit of angle is radians as blender API default setting
    params = [
        {
            'name': 'sun light 1', 'type': 'SUN',
            'location': np.array([3.638, 1.674, 4.329]), 'energy': 5.0, 'angle': 0.199
        },
        {
            'name': 'sun light 2', 'type': 'SUN',
            'location': np.array([0.449, -3.534, 1.797]), 'energy': 1.83, 'angle': 0.009
        },
        {
            'name': 'point light 1', 'type': 'POINT',
            'location': np.array([-2.163, -0.381, -2.685]), 'energy': 500
        }
    ]
    for param in params:
        light = bpy.data.lights.new(name=param['name'], type=param['type'])
        light.energy = param['energy']
        if param['type'] == 'SUN':
            light.angle = param['angle']
        light_obj = bpy.data.objects.new(name=param['name'], object_data=light)
        light_obj.location = param['location'] * scale_factor
        bpy.context.collection.objects.link(light_obj)


def track_object(obj: Object):
    """
    Let the camera track the specified object's center.
    By setting the tracking constraint, we can easily make the camera orient to
    the target object we want to render. This is less flexible but easier than
    setting the rotation manually.
    """
    camera: Object = bpy.data.objects['Camera']
    # the Track To constraint can keep the up direction of the camera better
    # than the Damp Track constraint, allowing placing the camera in the half-
    # space where x < 0
    camera.constraints.new('TRACK_TO')
    constraint = camera.constraints['Track To']
    constraint.target = obj
    constraint.track_axis = 'TRACK_NEGATIVE_Z'


def clear_imported_objects():
    """
    Remove the imported mesh and point cloud from the current scene, together
    with the materials automatically created by Blender when importing a mesh.
    """
    for obj in bpy.data.objects:
        # after application of geometry nodes, the point cloud data will also
        # be mesh
        if obj.type == 'MESH':
            logging.info(f'remove object {obj.name} from the current scene')
            bpy.data.objects.remove(obj)

    for material in bpy.data.materials:
        if material.name not in protected_material_names:
            logging.info(f'remove material {material.name}')
            bpy.data.materials.remove(material)


def launch_render(mesh1_path, mesh2_path, ibs_path, PID1_path, PID2_path):
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
    init_scene()
    create_materials()
    init_lights()

    camera_obj: Object = bpy.data.objects['Camera']
    camera_location = np.array([0.4, -0.5, 0.1]) * 2.3
    camera_obj.location = camera_location

    bpy.ops.wm.obj_import(filepath=mesh1_path)
    mesh1 = bpy.data.objects["scene1.1000_0"]
    mesh1.active_material = bpy.data.materials['mesh1']

    bpy.ops.wm.obj_import(filepath=mesh2_path)
    mesh2 = bpy.data.objects["scene1.1000_1"]
    mesh2.active_material = bpy.data.materials['mesh2']

    bpy.ops.wm.obj_import(filepath=ibs_path)
    ibs = bpy.data.objects["scene1.1000"]
    ibs.active_material = bpy.data.materials['ibs']

    bpy.ops.wm.obj_import(filepath=PID1_path)
    PID1 = bpy.data.objects["arrow1"]
    PID1.active_material = bpy.data.materials['PID1']

    bpy.ops.wm.obj_import(filepath=PID2_path)
    PID2 = bpy.data.objects["arrow2"]
    PID2.active_material = bpy.data.materials['PID2']

    for view_index, sign in enumerate(product(np.array([1, -1]), repeat=3)):
        camera_obj.location = camera_location * sign
        track_object(mesh1)
        bpy.context.scene.render.filepath = "./result/PID/{}".format(view_index)
        bpy.ops.render.render(write_still=True)
        break


if __name__ == '__main__':
    mesh1_path = r"D:\dataset\IBPCDC\mesh\scene1\scene1.1000_0.obj"
    mesh2_path = r"D:\dataset\IBPCDC\mesh\scene1\scene1.1000_1.obj"
    ibs_path = r"D:\dataset\IBPCDC\IBSMesh\scene1\scene1.1000.obj"
    PID1_path = r"data\arrow1.obj"
    PID2_path = r"data\arrow2.obj"

    launch_render(mesh1_path, mesh2_path, ibs_path, PID1_path, PID2_path)
