import hou
import os
# from PySide2 import QtGui, QtWidgets, QtCore
import pipe.gui.quick_dialogs as qd
import pipe.gui.select_from_list as sfl

from pipe.tools.houtools.utils.utils import *

from pipe.am.project import Project
from pipe.am.body import Body
from pipe.am.element import Element
from pipe.am.environment import Department
from pipe.am.environment import Environment


class Cloner:

    def __init__(self):
        self.item_gui = None
        self.modify_publish = None
        self.material_publish = None
        self.hair_publish = None
        self.cloth_publish = None
        environment = Environment()
        self.user = environment.get_user()

    def clone_asset(self, node=None):
        self.clone_hda(hda=node)

    def clone_tool(self, node=None):
        self.clone_hda(hda=node)

    def clone_shot(self):
        project = Project()

        asset_list = project.list_shots()
        self.item_gui = sfl.SelectFromList(l=asset_list, parent=houdini_main_window(), title="Select a shot to clone")
        self.item_gui.submitted.connect(self.shot_results)

    def shot_results(self, value):
        shot_name = value[0]
        project = Project()

        body = project.get_body(shot_name)
        element = body.get_element("lighting")

        self.publishes = element.list_publishes();
        print("publishes: ", self.publishes)

        # make the list a list of strings, not tuples
        self.sanitized_publish_list = []
        for publish in self.publishes:
            path = publish[3]
            file_ext = path.split('.')[-1]
            if not file_ext == "hip" and not file_ext == "hipnc":
                continue
            label = publish[0] + " " + publish[1] + " " + publish[2]
            self.sanitized_publish_list.append(label)

        self.item_gui = sfl.SelectFromList(l=self.sanitized_publish_list, parent=houdini_main_window(), title="Select publish to clone")
        self.item_gui.submitted.connect(self.publish_selection_results)

    def publish_selection_results(self, value):

        selected_publish = None
        for item in self.sanitized_publish_list:
            if value[0] == item:
                selected_publish = item

        selected_scene_file = None
        for publish in self.publishes:
            label = publish[0] + " " + publish[1] + " " + publish[2]
            if label == selected_publish:
                selected_scene_file = publish[3]

        if selected_scene_file is not None:
            if not os.path.exists(selected_scene_file):
                qd.error('Filepath doesn\'t exist')
                return
            else:
                hou.hipFile.load(selected_scene_file)

    def clone_hda(self, hda=None):
        project = Project()

        asset_list = project.list_props_and_characters()
        self.item_gui = sfl.SelectFromList(l=asset_list, parent=houdini_main_window(), title="Select an asset to clone")
        self.item_gui.submitted.connect(self.asset_results)

    def asset_results(self, value):
        print("Selected asset: ", value[0])
        filename = value[0]

        project = Project()
        self.body = project.get_body(filename)

        self.modify_element = self.body.get_element("modify")
        self.material_element = self.body.get_element("material")
        self.hair_element = self.body.get_element("hair")
        self.cloth_element = self.body.get_element("cloth")

        self.filepath = self.body.get_filepath()

        modify_publish = self.modify_element.get_last_publish()
        material_publish = self.material_element.get_last_publish()
        hair_publish = self.hair_element.get_last_publish()
        cloth_publish = self.cloth_element.get_last_publish()

        if not modify_publish and not material_publish and not hair_publish and not cloth_publish:
            department_paths = None
        else:
            department_paths = {}

        if(modify_publish):
            self.modify_publish = modify_publish[3]
            department_paths['modify'] = self.modify_publish
        if(material_publish):
            self.material_publish = material_publish[3]
            department_paths['material'] = self.material_publish
        if(hair_publish):
            self.hair_publish = hair_publish[3]
            department_paths['hair'] = self.hair_publish
        if(cloth_publish):
            self.cloth_publish = cloth_publish[3]
            department_paths['cloth'] = self.cloth_publish

        from pipe.tools.houtools.assembler.assembler import Assembler  # put import here to remove cross import issue FIXME
        node, created_instances =  Assembler().create_hda(filename, body=self.body, department_paths=department_paths)
        layout_object_level_nodes()

        return node, created_instances
