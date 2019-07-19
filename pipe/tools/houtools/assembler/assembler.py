'''
    Hunter Tinney
    ================
    Intro to Pipe V2
    ================
    I designed this new procedure to minimize dependencies and allow for easier changes to our Houdini pipeline. With the old
    code, whenever we assembled an asset, we were locked into a specific configuration for that asset and would need to
    re-assemble if anything changed. Now, we abstracted out a lot of the functionality into nodes like DCC Import, DCC Geo,
    DCC Character, DCC Set, etc, so that way we push changes to all the assets at once (for example, if we were to switch to
    USD mid production.)
    Please pay attention to the differences in each of these nodes. Functional HDAs and Template HDAs are very different (for
    example, a Template HDA is never intended to be tabbed into Houdini, it simply serves as a template for creating other
    HDAs.)
    =======================
    Dynamic Content Subnets
    =======================
    These HDAs are simply subnets that hold other HDAs. The difference is that they will destroy and re-tab in the HDAs inside
    of them, based on what asset is loaded. Let's say Swingset_001 is loaded, and we switch to Swingset_002. DCC Geo would
    switch out all the Content HDAs (material, etc.) of Swingset_001 with the content of Swingset_002. A description of
    Content HDAs is found in the "Content HDAs" section.
        NAME         Subnet Type    Description
     ----------------------------------------------------------------------------------------------------------------------------
    | DCC Geo          OBJ>SOP    Houses the other nodes at the most basic level, good enough for props and character meshs.     |
    | DCC Character    OBJ>OBJ    Houses a DCC Geo, a Hair asset and a Cloth asset. It's our verion of a character group.        |
    | DCC Set          OBJ>OBJ    Reads from a JSON file (exported from Maya) that contains positions of assets in a set. It     |
    |                                 tabs them all in as DCC Geos, and then offsets them to their correct                       |
    |                                 positions/scales/rotates.                                                                  |
     ----------------------------------------------------------------------------------------------------------------------------
    ===============
    Functional HDAs
    ===============
    These HDAs take some of the more common procedures and generalize them. That way, if we ever change the way we import
    geometry into Houdini, we could make a simple change to DCC Import (for example) and that change would propagate to all
    the assets that contain DCC Import.
        NAME         Node Type    Description
     ----------------------------------------------------------------------------------------------------------------------------
    | DCC Import       SOP        Has all the functionality to bring in assets from the pipe via Alembic. This functionality     |
    |                                 could be swapped out for USD Import at a future date.                                      |
    | DCC Mat. Assign  SOP        Full Name: DCC Material Assign. Basically the Material SOP, but provides a way of switching    |
    |                                 between material options.                                                                  |
    | DCC Shopnet      SOP        Provides a way for us to supply default material setups for shading artists.                   |
    | DCC Primvars     SOP        Allows for quick selection of primitive/point/vertex groups and using them as masks for        |
    |                                 RenderMan shaders.                                                                         |
     ----------------------------------------------------------------------------------------------------------------------------
    =============
    Template HDAs
    =============
    These HDAs will never be tabbed in directly. They simply serve as template data that can be used to create other HDAs. Please
    see the section on "Content HDAs" below for an explanation of the process.
        NAME         Node Type    Description
     ----------------------------------------------------------------------------------------------------------------------------
    | DCC Material     SOP         Holds a Primvars, Mat. Assign and Shopnet Functional HDA. Intended for shading.                |
    | DCC Modify       SOP         Modifies incoming geometry, intended for any geometry fixes that need to be done in Houdini.   |
    | DCC Hair         OBJ>OBJ     Holds all hair subnets for a given character. It should take a DCC Geo as an input. It should  |
    |                                  also have simulation parameters promoted to the top-level, which is done by the artist.    |
    | DCC Cloth        OBJ>OBJ     Holds all cloth subnets for a given character. It should take a DCC Geo as an input. It should |
    |                                  also have simulation parameters promoted to the top-level, which is done by the artist.    |
     ----------------------------------------------------------------------------------------------------------------------------
    ============
    Content HDAs
    ============
    Content HDAs are simply instances of Template HDAs. They could be materials, modify nodes, hair subnets, cloth subnets, etc.
    Typically their names will be something like "swingset_001_material". These HDAs are created on an as-needed basis for
    each asset in the pipe (although most assets will have a material.) They are created by duplicating the Template HDA
    (which is stored in the houdini-tools package.) We store that HDA in the User folder of the artist. Then, when they are ready,
    we publish it to the pipe as a new HDA. Because it is a new HDA definition, we are locked into its functionality unless the
    asset is re-created. This is a bit unfortunate (it is the equivalent to copying and pasting similar code instead of abstracting
    it out into a shared function/class.) However, we have ensured that most of the functionality that we would like to tweak has
    been abstracted out into Functional HDAs that are inside each of these nodes. That way, if we change the functional HDAs, we
    will propagate those changes to all the Content HDAs that contain them. If you find a better way to emulate polymorphism in
    HDAs, please update this!!
    ========
    Updating
    ========
    The different update modes have different meanings.
        smart: Sets will add new components, and delete old ones. Props and characters will update everything except checked out items and shot_modeling.
        clean: Everything is deleted and re-tabbed in, ensuring it is 100% synced. Does not allow for overrides.
        frozen: Nothing happens.
'''
import hou, sys, os, json #??
from pipe.am.project import Project
from pipe.am.environment import Environment, Department
from pipe.am.element import Element
from pipe.am.body import Body, Asset, Shot, AssetType

import pipe.gui.select_from_list as sfl
import pipe.gui.quick_dialogs as qd
import pipe.gui.write_message as wm
from pipe.tools.houtools.utils import *

# # CHECKOUT BREAKS IN NON_GUI MODE
# import checkout #old pipe file
#
# import publish #old pipe file
# # DEBUGGING ONLY
# import inspect #no idea
# import datetime #never


class UpdateModes:
    SMART = "smart"
    CLEAN = "clean"
    FROZEN = "frozen"

    @classmethod
    def list_modes(cls):
        return [cls.SMART, cls.CLEAN, cls.FROZEN]

    @classmethod
    def mode_from_index(cls, index):
        return [cls.SMART, cls.CLEAN, cls.FROZEN][index]


class Assembler:
    def __init__(self):
        self.asset_gui = None

        # The order in which these nodes appear is the order they will be created in
        self.dcc_geo_departments = [Department.MODIFY, Department.MATERIAL]
        self.dcc_character_departments = [Department.HAIR, Department.CLOTH]

        # The source HDA's are currently stored inside the pipe source code.
        self.hda_path = Environment().get_otl_dir()

        # We define the template HDAs definitions here, for use in the methods below
        self.hda_definitions = {
            Department.MATERIAL: hou.hdaDefinition(hou.sopNodeTypeCategory(), "dcc_material", os.path.join(self.hda_path, "dcc_material.hda")),
            Department.MODIFY: hou.hdaDefinition(hou.sopNodeTypeCategory(), "dcc_modify", os.path.join(self.hda_path, "dcc_modify.hda")),
            Department.HAIR: hou.hdaDefinition(hou.objNodeTypeCategory(), "dcc_hair", os.path.join(self.hda_path, "dcc_hair.hda")),
            Department.CLOTH: hou.hdaDefinition(hou.objNodeTypeCategory(), "dcc_cloth", os.path.join(self.hda_path, "dcc_cloth.hda"))
        }

        # By default, we ignore "Asset Controls", so that we can put things in there without them being promoted.
        # See: inherit_parameters() method
        self.default_ignored_folders = ["Asset Controls"]

    def run(self):
        # step 1: Select the body
        # step 2: select the element to assemble
        # step 3: assemble the element
        project = Project()
        asset_list = project.list_assets()

        self.asset_gui = sfl.SelectFromList(l=asset_list, parent=houdini_main_window(), title="Select an asset to assemble")
        self.asset_gui.submitted.connect(self.asset_results)

    def asset_results(self, asset):
        self.selected_asset = asset[0]

        project = Project()
        self.body = project.get_body(self.selected_asset)

        department_list = self.body.default_departments()

        self.element_gui = sfl.SelectFromList(l=department_list, parent=houdini_main_window(), title="Select element to assemble")
        self.element_gui.submitted.connect(self.element_results)

    def element_results(self, value):
        self.chosen_department = value[0]

        self.create_hda(self.selected_asset, self.chosen_department)

    #sys.stdout = open(os.path.join(Project().get_users_dir(), Project().get_current_username(), "houdini_console_output.txt"), "a+")

    def error_message(self, message):
        qd.error(message)

    def lineno(self):
        return inspect.currentframe().f_back.f_lineno

    def method_name(self):
        return sys._getframe(1).f_code.co_name

    def super_print(self, message):
        with open(os.path.join(Project().get_users_dir(), Project().get_current_username(), "houdini_log.txt"), "a+") as f:
            print(message)
            sys.stdout.flush()
            f.write("\n" + str(datetime.datetime.now()) + "\n")
            f.write(message)
            f.flush()

    '''
        Easily callable method, meant for tool scripts
    '''
    def tab_in(self, parent, asset_name, already_tabbed_in_node=None, excluded_departments=[]):
        print "Creating node for {0}".format(asset_name)
        body = Project().get_body(asset_name)
        if body is None or not body.is_asset():
            self.error_message("Pipeline error: This asset either doesn't exist or isn't an asset.")
            return
        if body.get_type() == AssetType.CHARACTER:
            return dcc_character(parent, asset_name, already_tabbed_in_node, excluded_departments)
        elif body.get_type() == AssetType.PROP:
            return self.dcc_geo(parent, asset_name, already_tabbed_in_node, excluded_departments)
        elif body.get_type() == AssetType.SET:
            return dcc_set(parent, asset_name, already_tabbed_in_node)
        else:
            self.error_message("Pipeline error: this asset isn't a character, prop or set.")
            return

    '''
        Easily callable method, meant for tool scripts
    '''
    def update_contents(self, node, asset_name, mode=UpdateModes.SMART):
        if node.type().name() == "dcc_set":
            self.update_contents_set(node, asset_name, mode=mode)
        elif node.type().name() == "dcc_character":
            self.update_contents_character(node, asset_name, mode=mode)
        elif node.type().name() == "dcc_geo":
            self.update_contents_geo(node, asset_name, mode=mode)

    def subnet_type(self, asset_name):
        body = Project().get_body(asset_name)
        if body is None or not body.is_asset():
            self.error_message("Pipeline error: This asset either doesn't exist or isn't an asset.")
            return
        if body.get_type() == AssetType.CHARACTER:
            return "dcc_character"
        elif body.get_type() == AssetType.PROP:
            return "dcc_geo"
        elif body.get_type() == AssetType.SET:
            return "dcc_set"
        else:
            self.error_message("Pipeline error: this asset isn't a character, prop or set.")
            return



    '''
        This function tabs in a DCC Set and fills its contents with other DCC Geo nodes based on JSON data
    '''

    def dcc_set(self, parent, set_name, already_tabbed_in_node=False, mode=UpdateModes.CLEAN):

        # Check if it's a set and that it exists
        body = Project().get_body(set_name)
        if not body.is_asset() or not body.get_type() == AssetType.SET:
            self.error_message("Must be a set.")

        node = already_tabbed_in_node if already_tabbed_in_node else parent.createNode("dcc_set")
        try:
            node.setName(set_name)
        except:
            node.setName(set_name + "_1", unique_name=True)
        # Update contents in the set
        self.update_contents_set(node, set_name, mode)
        return node


    # Utility function to find if a node's asset and version number match an entry in the set's JSON
    def matches_reference(self, child, reference):

        # Grab data off the node. This data is stored as a key-value map parameter
        data = child.parm("data").evalAsJSONMap()

        print "{0}:\n\t checked {1} against {2}".format(str(child), str(data), str(reference))

        # If it matches both the asset_name and version_number, it's a valid entry in the list
        if data["asset_name"] == reference["asset_name"] and data["version_number"] == str(reference["version_number"]):
            print "\tand it matched"
            return True
        else:
            print "\tand it didn't match"
            return False

    '''
        Updates the contents of a set
    '''
    def update_contents_set(self, node, set_name, mode=UpdateModes.SMART):

        # Check if reference file exists
        set_file = os.path.join(Project().get_assets_dir(), set_name, "model", "main", "cache", "whole_set.json")

        # Error checking
        try:
            with open(set_file) as f:
                set_data = json.load(f)
        except Exception as error:
            self.error_message("No valid JSON file for " + set_name)
            return

        node.parm("asset_name").set(set_name)
        data = node.parm("data").evalAsJSONMap()
        data["asset_name"] = set_name
        node.parm("data").set(data)

        inside = node.node("inside")

        # Grab current DCC Dynamic Content Subnets that have been tabbed in
        current_children = [child for child in inside.children() if child.type().name() in ["dcc_set", "dcc_character", "dcc_geo"]]

        # Smart updating will only destroy assets that no longer exist in the Set's JSON list
        if mode == UpdateModes.SMART:

            non_matching = [child for child in current_children if len([reference for reference in set_data if matches_reference(child, reference)]) == 0]
            for non_match in non_matching:
                non_match.destroy()

        # Clean updating will destroy all children.
        elif mode == UpdateModes.CLEAN:

            # Clear all child nodes.
            inside.deleteItems(inside.children())

        # Grab current children again
        current_children = [child for child in inside.children() if child.type().name() in ["dcc_set", "dcc_character", "dcc_geo"]]

        # Tab-in/update all assets in list
        for reference in set_data:

            body = Project().get_body(reference["asset_name"])

            if body is None:
                print 'Error on: ', reference["asset_name"]
                continue
            if not body.is_asset() or body.get_type() == AssetType.SET:
                continue

            # Tab the subnet in if it doesn't exist, otherwise update_contents
            subnet = next((child for child in current_children if matches_reference(child, reference)), None)
            if subnet is None:
                subnet = self.dcc_geo(inside, reference["asset_name"])
            else:
                self.update_contents(subnet, reference["asset_name"], mode)

            # Try to not override parameters in the set
            if mode == UpdateModes.SMART:
                for key in reference:
                    # Pull parm from node
                    parm = subnet.parm(key)
                    # If a non-default value is there, it most likely came from a user. Don't overwrite it.
                    if parm and parm.isAtDefault():
                        parm.set(reference[key])

            # Override parameters in the set
            elif mode == UpdateModes.CLEAN:
                newparms = {"asset_name" : reference["asset_name"], "version_number" : reference["version_number"] }
                subnet.setParms(newparms)

            # Set the set accordingly

            subnet.parm("space").set("set")
            subnet.parm("set").set(set_name)
            #subnet.parm("update_mode").setExpression("ch(\"../../update_mode\")", language=hou.exprLanguage.Hscript)
            subnet.parm("update_mode").set(UpdateModes.list_modes().index(mode))
            # Set the data
            subnet.parm("data").set({
                "asset_name": str(reference["asset_name"]),
                "version_number" : str(reference["version_number"])
            })

        inside.layoutChildren()

    '''
        Cache all the contents of the set for faster cooking
    '''
    def set_cache_path(self, parent, child):
        data = child.parm("data").evalAsJSONMap()
        cachepath = parent.parm("cachepath").evalAsString() + "/" + child.name()
        child.parm("cachepath").set(cachepath)

    def set_read_from_disk(self, node, on_or_off):
        inside = node.node("inside")
        children = [child for child in inside.children() if child.type().name() == "dcc_geo"]

        for child in children:
            set_cache_path(node, child)
            child.parm("read_from_disk").set(on_or_off)

    def reload_from_disk(self, node):
        inside = node.node("inside")
        children = [child for child in inside.children() if child.type().name() == "dcc_geo"]

        for child in children:
            set_cache_path(node, child)
            child.parm("reload_from_disk").pressButton()

    def save_to_disk(self, node):
        inside = node.node("inside")
        children = [child for child in inside.children() if child.type().name() == "dcc_geo"]

        for child in children:
            set_cache_path(node, child)
            read_from_disk_value = child.parm("read_from_disk").evalAsInt()
            child.parm("read_from_disk").set(0)
            child.parm("save_to_disk").pressButton()
            child.parm("read_from_disk").set(read_from_disk_value)


    '''
        This function tabs in a DCC Character node and fills its contents with the appropriate character name.
        Departments is a mask because sometimes we tab this asset in when we want to work on Hair or Cloth, and don't want the old ones to be there.
    '''
    def dcc_character(self, parent, asset_name, already_tabbed_in_node=None, excluded_departments=[], mode=UpdateModes.CLEAN, shot=None):

        # Set up the body/elements and make sure it's a character
        body = Project().get_body(asset_name)
        if not body.is_asset() or not body.get_type() == AssetType.CHARACTER:
            self.error_message("Must be a character.")
            return None

        # If there's an already tabbed in node, set it to that node
        node = already_tabbed_in_node if already_tabbed_in_node else parent.createNode("dcc_character")
        try:
            node.setName(asset_name.title())
        except:
            node.setName(asset_name.title() + "_1", unique_name=True)
        node.parm("asset_name").set(asset_name)

        # Set the asset_name data tag
        data = node.parm("data").evalAsJSONMap()
        data["asset_name"] = asset_name
        node.parm("data").set(data)

        # Set the contents to the character's nodes
        self.update_contents_character(node, asset_name, excluded_departments, mode, shot)
        return node

    '''
        This function sets the inner contents of a DCC Character node.
    '''
    def update_contents_character(self, node, asset_name, excluded_departments=[], mode=UpdateModes.SMART, shot=None):

        # Set up the body/elements and make sure it's a character. Just do some simple error checking.
        body = Project().get_body(asset_name)
        if not body.is_asset() or body.get_type() != AssetType.CHARACTER or "dcc_character" not in node.type().name():
            self.error_message("Must be a character.")
            return None

        # Reset the data parm
        data = node.parm("data").evalAsJSONMap()
        data["asset_name"] = asset_name
        node.parm("data").set(data)

        inside = node.node("inside")

        # Make sure the geo is set correctly
        geo = inside.node("geo")
        if geo is not None:
            if mode == UpdateModes.SMART:
                self.update_contents_geo(geo, asset_name, excluded_departments, mode)

            elif mode == UpdateModes.CLEAN:
                geo.destroy()
                geo = self.dcc_geo(inside, asset_name, excluded_departments=excluded_departments, character=True)
        else:
            geo = self.dcc_geo(inside, asset_name, excluded_departments=excluded_departments, character=True)

        # Tab in each content HDA based on department
        for department in self.dcc_character_departments:
            # If the department is not excluded, tab-in/update the content node like normal
            if department not in excluded_departments:

                self.update_content_node(node, inside, asset_name, department, mode)

            # If the department is excluded, we should delete it.
            elif mode == UpdateModes.CLEAN:

                self.destroy_if_there(inside, department)


        inside.layoutChildren()

        geo.parm("version_number").setExpression("ch(\"../../version_number\")", language=hou.exprLanguage.Hscript)

        # If this character is being animated, set parms accordingly
        if shot is not None:
            geo.parm("space").set("anim")
            geo.parm("asset_department").set("rig")
            geo.parm("shot").set(shot)

        return node

    '''
        This function tabs in a DCC Geo node and fills its contents according to the appropriate asset name.
    '''
    def dcc_geo(self, parent, asset_name, already_tabbed_in_node=None, excluded_departments=[], character=False, mode=UpdateModes.CLEAN):
        # Set up the body/elements and check if it's an asset.
        body = Project().get_body(asset_name)
        if not body.is_asset():
            self.error_message("Must be an asset.")
            return None

        # Set up the nodes, name geo
        node = already_tabbed_in_node if already_tabbed_in_node else parent.createNode("dcc_geo")
        if character:
            node.setName("geo")
        else:
            try:
                node.setName(asset_name.title())
            except:
                node.setName(asset_name.title() + "_1", unique_name=True)

        # Set the asset_name data tag
        data = node.parm("data").evalAsJSONMap()
        data["asset_name"] = asset_name
        node.parm("data").set(data)

        # Set the contents to the nodes that belong to the asset
        self.update_contents_geo(node, asset_name, excluded_departments, mode)

        return node

    '''
        This function sets the dynamic inner contents of a DCC Geo node.
    '''
    def update_contents_geo(self, node, asset_name, excluded_departments=[], mode=UpdateModes.SMART):

        # Set up the body/elements and make sure it's a character. Just do some simple error checking.
        body = Project().get_body(asset_name)
        if body is None:
            self.error_message("Asset doesn't exist.")
            return None
        if not body.is_asset() or body.get_type() == AssetType.SET or "dcc_geo" not in node.type().name():
            self.error_message("Must be a prop or character.")
            return None

        # Get interior nodes
        importnode = node.node("import")
        inside = node.node("inside")

        # Set the asset_name and reload
        if node.parm("asset_name").evalAsString() != asset_name:
            node.parm("asset_name").set(asset_name)
        #importnode.parm("reload").pressButton()

        # Tab in each content HDA based on department
        for department in self.dcc_geo_departments:
            # If the department is not excluded, tab-in/update the content node like normal
            if department not in excluded_departments:
                self.update_content_node(node, inside, asset_name, department, mode, inherit_parameters = department == Department.MODIFY)

            # If the department is excluded, we should delete it.
            elif mode == UpdateModes.CLEAN:
                self.destroy_if_there(inside, department)


        inside.layoutChildren()

        return node

    '''
        Creates new content HDAs
    '''
    def create_hda(self, asset_name, department, already_tabbed_in_node=None):

        # Check if this body is an asset. If not, return error.
        body = Project().get_body(asset_name)
        if not body.is_asset():
            self.error_message("Must be an asset of type PROP or CHARACTER.")
            return None

        # Check if it is a set.
        if body.get_type() == AssetType.SET:
            self.error_message("Asset must be a PROP or CHARACTER.")
            return None

        # Check if the user is trying to create a Hair or Cloth asset for a Prop on accident.
        if body.get_type() == AssetType.PROP and (department == Department.HAIR or department == Department.CLOTH):
            self.error_message("Hair and cloth should only be made for characters.")
            return None

        # Create element if does not exist.
        element = body.get_element(department, name=Element.DEFAULT_NAME, force_create=True)

        # TODO: Get rid of this ugly hotfix
        # !!! HOTFIX !!!
        # Material was previously used as an AssetElement, but now is being treated like an HDAElement.
        # This will convert it's file extension to .hdanc. (Before, it's extension was "").
        element._datadict[Element.APP_EXT] = element.create_new_dict(Element.DEFAULT_NAME, department, asset_name)[Element.APP_EXT]
        element._update_pipeline_file()
        # !!! END HOTFIX !!!

        # Check out the department.
        username = Project().get_current_username()
        checkout_file = element.checkout(username)

        # Tab in the parent asset that will hold this checked out HDA
        node = already_tabbed_in_node if already_tabbed_in_node else self.tab_in(hou.node("/obj"), asset_name, excluded_departments=[department])

        # If it's a character and it's not a hair or cloth asset, we need to reach one level deeper.
        if body.get_type() == AssetType.CHARACTER and department not in self.dcc_character_departments:
            inside = node.node("inside/geo/inside")
        else:
            inside = node.node("inside")

        # CREATE NEW HDA DEFINITION
        operator_name = element.get_parent() + "_" + element.get_department()
        operator_label = (asset_name.replace("_", " ") + " " + element.get_department()).title()

        self.hda_definitions[department].copyToHDAFile(checkout_file, operator_name, operator_label)
        hda_type = hou.objNodeTypeCategory() if department in self.dcc_character_departments else hou.sopNodeTypeCategory()
        hou.hda.installFile(checkout_file)
        hda_definition = hou.hdaDefinition(hda_type, operator_name, checkout_file)
        hda_definition.setPreferred(True)

        # Tab an instance of this new HDA into the asset you are working on
        try:
            hda_instance = inside.createNode(asset_name + "_" + department)
            print('created hda instance')
        except Exception as e:
            self.error_message("HDA Creation Error. " + asset_name + "_" + department + " must not exist.")
        hda_instance.setName(department)
        self.tab_into_correct_place(inside, hda_instance, department)
        hda_instance.allowEditingOfContents()
        hda_instance.setSelected(True, clear_all_selected=True)

        return hda_instance

    '''
        Updates a content node.
    '''
    def update_content_node(self, parent, inside, asset_name, department, mode=UpdateModes.SMART, inherit_parameters=False, ignore_folders=["Asset Controls"]):

        # See if there's a content node with this department name already tabbed in.
        content_node = inside.node(department)

        # If the content node exists.
        if content_node is not None:
            # Delete it if we're in clean mode
            if mode == UpdateModes.CLEAN:
                content_node.destroy()
                content_node = None

        # Check if there's a published asset with this name
        try:
            is_published = self.published_definition(asset_name, department)
        except:
            is_published = False

        # If there isn't a published asset, delete the impostor!
        if not is_published and content_node:
            content_node.destroy()

        # This line checks to see if there's a published HDA with that name.
        elif is_published and not content_node:
            # Only create it if it's in the pipe.
            try:
                content_node = inside.createNode(asset_name + "_" + department)
            except:
                content_node = None
            # Check if node was successfully created
            if content_node:
                self.tab_into_correct_place(inside, content_node, department)
                content_node.setName(department)
                # Some nodes will promote their parameters to the top level
                if inherit_parameters:
                    self.inherit_parameters_from_node(parent, content_node, mode, ignore_folders)


        return content_node

    '''
        Destroys unwanted content nodes if they remain in a network
    '''
    def destroy_if_there(self, inside, department):
        node = inside.node(department)
        if node is not None:
            node.destroy()
        node = None

    '''
        Check if a definition is the published definition or not
    '''
    def published_definition(self, asset_name, department):
        # Set the node type correctly
        category = hou.objNodeTypeCategory() if department in self.dcc_character_departments else hou.sopNodeTypeCategory()
        hou.hda.reloadAllFiles()

        # Get the HDA File Path
        hda_name = asset_name + "_" + department
        hda_file = hda_name + "_main.hdanc"
        new_hda_path = os.path.join(Project().get_project_dir(), "production", "hda", hda_file)
        old_hda_path = os.path.join(Project().get_project_dir(), "production", "otls", hda_file)

        hda_path = ""
        # Does it exist?
        if os.path.islink(new_hda_path):
            hda_path = os.readlink(new_hda_path)
        elif os.path.islink(old_hda_path):
            hda_path = os.readlink(old_hda_path)
        else:
            return False

        # If it does, grab the definition
        hou.hda.installFile(hda_path)
        hda_definition = hou.hdaDefinition(category, hda_name, hda_path)

        # If the definition failed for some reason, don't tab it in.
        if hda_definition is not None:
            hda_definition.setPreferred(True)
            return True
        else:
            return False

    '''
        Promote parameters from an inner node up to an outer node.
    '''
    def inherit_parameters_from_node(self, upper_node, inner_node, mode=UpdateModes.SMART, ignore_folders=[]):

        # Get the lists of parms
        upper_spare_parms = inner_node.spareParms()
        inner_parms = inner_node.parms()
        inner_parm_names = [parm.name() for parm in inner_parms]

        # Only remove the spare parms that are no longer on the inner_node
        if mode == UpdateModes.SMART:
            for upper_spare_parm in upper_spare_parms:
                if upper_parm.name() not in inner_parm_names:
                    upper_node.removeSpareParmTuple(inner_parm.parmTemplate())

        # Clean all spare parms
        elif mode == UpdateModes.CLEAN:
            upper_node.removeSpareParms()

        # Else, don't do anything
        elif mode == UpdateModes.FROZEN:
            return

        for inner_parm in inner_parms:

            # This isn't very elegant, but I need to check if any containing folders contain any substrings from ignore_folders
            in_containing_folder = False
            for folder in ignore_folders:
                for containingFolder in inner_parm.containingFolders():
                    if folder in containingFolder:
                        in_containing_folder = True
                        break
                if in_containing_folder:
                    break
            if in_containing_folder:
                continue
            # That's the end of the non-elegant solution

            # If not in the ignored folders, then either set the value or promote it
            upper_parm = upper_node.parm(inner_parm.name())
            if upper_parm is not None:
                upper_parm.set(inner_parm.eval())
            else:
                upper_node.addSpareParmTuple(inner_parm.parmTemplate(), inner_parm.containingFolders(), True)

    '''
        Helper function for create_hda()
    '''
    def tab_into_correct_place(self, inside, node, department):

        # If the node belongs inside a DCC Character, do the following
        if department in self.dcc_character_departments:

            # Hair and Cloth assets should be connected to geo. If it doesn't exist, throw an error.
            geo = inside.node("geo")
            if geo is None:
                error_message("There should be a geo network. Something went wrong.")
                return

            # Attach the Hair or Cloth asset to the geo network.
            node.setInput(0, geo)

        # If the node belongs inside a DCC Geo, do the following
        else:

            # Shot_modeling and geo are our way of knowing where to insert nodes. If either of them is null, throw an error.
            geo = inside.node("geo")
            shot_modeling = inside.node("shot_modeling")
            if shot_modeling is None or geo is None:
                self.error_message("There should be a shot_modeling and geo network. Something went wrong.")
                return None

            # If we're inserting a modify node, do the following
            if department == Department.MODIFY:

                # If there is a material node, put the modify node in between material and geo.
                material = inside.node("material")
                if material is not None:
                    node.setInput(0, geo)
                    material.setInput(0, node)

                # Else, stick it between geo and shot_modeling.
                else:
                    node.setInput(0, geo)
                    shot_modeling.setInput(0, node)

            # If we're inserting a material node, do the following
            elif department == Department.MATERIAL:

                # If there is a modify node, put the material node in between modify and shot_modeling.
                modify = inside.node("modify")
                if modify is not None:
                    node.setInput(0, modify)
                    shot_modeling.setInput(0, node)

                # Else, stick it between geo and shot_modeling.
                else:
                    node.setInput(0, geo)
                    shot_modeling.setInput(0, node)


        inside.layoutChildren()
        return node

    def rebuildAllAssets(self):
        # Recursively go through each node, and push the build button if exists.

        # Initialize stack.
        stack = []

        # Start at root.
        stack.append(hou.node("/obj"))

        # While stack is not empty,
        while len(stack) > 0:
            # Pop off the latest entry
            parent = stack.pop()

            # Re-grab children each generation (so that the results of the previous "build" apply
            for child in parent.children():

                # Add to stack
                stack.append(child)

                # Check if it is a dynamic content subnet, and press the build button. Else, continue.
                type_name = child.type().name()
                if "dcc_geo" in type_name or "dcc_character" in type_name or "dcc_set" in type_name:
                    build_button = child.parm("build")
                    if build_button:
                        build_button.pressButton()
    '''
    def convertV1_to_V2(nodes):

        for node in nodes:

            if node.type().name() == "dcc_geo":
                node = node.parent().createNode(node.parm("asset_name").evalAsString() + "_main")

            geometry = next((child for child in node.children() if child.type().name() == "geo"), None)
            shopnet = next((child for child in node.children() if child.type().name() == "shopnet"), None)

            print geometry
            # If there's not a geometry network inside that has the same name as the operator,
            # then it's probably not an old DCC asset that we know how to deal with, so skip it.

            if not geometry or not shopnet or geometry.name() not in node.type().name() or geometry.name() not in shopnet.name():
                continue

            root = geometry.node("hide_geo")
            print root
            if not root:
                continue

            checkout.checkout_asset_go(node)

            parent = node.parent()
            output = geometry.displayNode()

            # Find descendants of the root node, these are the old components
            descendants = []
            stack = []
            stack.append(root)
            while len(stack) > 0:
                ancestor = stack.pop()
                descendants.append(ancestor)
                for output in ancestor.outputs():
                    stack.append(output)




            # Tab in a V2 Dynamic Content Subnet
            asset_name = geometry.name()

            new_node=None
            new_node = tab_in(parent, asset_name, excluded_departments=[Department.MODIFY, Department.MATERIAL])

            # Copy old components into a modify node
            modify_node = create_hda(asset_name, Department.MODIFY, already_tabbed_in_node=new_node)

            display_out=modify_node.displayNode()
            copied_nodes=hou.copyNodesTo(descendants, modify_node)

            ## TODO: PARSE THROUGH NODES AND CONNECT TO OUTPUT


            hide_geo=None
            descend_out=None
            mat_node=None

            for copied_node in copied_nodes:
                if 'switch' in copied_node.type().name():
                    hide_geo=copied_node
                elif 'material' == copied_node.type().name():
                    mat_node=copied_node
                elif 'output' == copied_node.type().name():
                    descend_out=copied_node
                elif 'null' == copied_node.type().name() and 'OUT' in copied_node.name():
                    descend_out=copied_node



            hide_geo.setInput(0,modify_node.indirectInputs()[0])
            display_out.setInput(0,descend_out)



            display_out.setDisplayFlag(True)
            display_out.setRenderFlag(True)

            display_out.setInput(0,descend_out)
            modify_node.layoutChildren()

            descend_out.destroy()
            hide_geo.destroy()
            mat_node.destroy()




            # Copy old components into a material node
            material_node = create_hda(asset_name, Department.MATERIAL, already_tabbed_in_node=new_node)
            hou.copyNodesTo(shopnet.children(), material_node.node("shopnet/shaders"))

            # Do material assignment as much as possible
            # TODO: riley

            material= geometry.node("material1")
            num_groups=material.evalParm('num_materials')

            #map of material to string of group mask
            info={}

            for i in range(1,num_groups+1):
                group=material.evalParm('group'+str(i))
                mat=material.evalParm('shop_materialpath'+str(i))

                mat='../shopnet/shaders/'+'/'.join(mat.split('/')[-2:])

                if mat in info:
                    info[mat]+=' '+group
                else:
                    info[mat]=group


            mat_assign=material_node.node('material_assign')
            mat_assign.parm('num_materials').set(len(info))


            #transfer material assignments
            for i,key in enumerate(info):
                mat_assign.parm('group'+str(i+1)).set(info[key])
                mat_assign.parm('material_options'+str(i+1)).set(1)
                mat_assign.parm('mat_option'+str(i+1)+'_1').set(key)



            # Name the nodes
            node.setName(asset_name + "_old")
            new_node.setName(asset_name + "_new")

            # Put them both into a network box, so we can see that they are related.
            box = parent.createNetworkBox()
            box.addItem(node)
            box.addItem(new_node)
            box.fitAroundContents()
            box.setComment(node.type().name().replace("_main", "").title())
            parent.layoutChildren()
    '''

    '''
        The user has selected a bunch of nodes/network boxes and this should sort out which is which,
        so that it can commit the conversions for those groups.
    '''
    def commit_conversions(self):

        # Find all boxes that have nodes that were made by the conversion script
        boxes = []
        for item in hou.selectedItems():
            if not isinstance(item, hou.NetworkBox):
                continue

            # If the box doesn't have two nodes in it, it's definitely not ours
            nodes = item.nodes()
            if len(nodes) != 2:
                continue

            # If neither is named _new and/or neither is named _old, it's not one of ours
            if not "_new" in nodes[0].name() and not "_new" in nodes[1].name():
                continue
            if not "_old" in nodes[0].name() and not "_old" in nodes[1].name():
                continue

            # If the assets are not named the same, it's not one of ours
            print nodes[0].name()[:-4]
            print nodes[1].name()[:-4]

            if nodes[0].name()[:-4] != nodes[1].name()[:-4]:
                continue

            # If it passed the tests, add it to the list of network boxes we can work with
            boxes.append(item)

        print boxes

        # Don't go on unless there's a valid network box
        if len(boxes) < 1:
            self.error_message("There aren't any network boxes created by the conversion script.")
            return

        for box in boxes:
            old_node = next((node for node in box.nodes() if "_old" in node.name()), None)
            new_node = next((node for node in box.nodes() if "_new" in node.name()), None)

            old_hda = old_node.type().definition()
            old_hda.setIcon(Environment().get_project_dir() + '/pipe/tools/_resources/1.png')

            publish.non_gui_publish_go(old_node, "Converted to V2")
            for child in new_node.allSubChildren():
                if "_material" in child.type().name() or "_modify" in child.type().name():
                    publish.non_gui_publish_go(child, "Converted from V1")

            # commit old_node
            # commit new_node
    '''this function has the set look for shot specific information, specifically if it has any animated objects'''
    def shotInfo(self, shot_name):
        print shot_name
        shot_file = os.path.join(Project().get_shots_dir(),shot_name, "anim", "main", "cache", "animated_props.json")

        shot_data=None
        try:
            with open(shot_file) as f:
                shot_data = json.load(f)
        except Exception as error:
            self.error_message("No valid JSON file for " + shot_name)
            return

        for asset in shot_data:
            try:
                node=hou.node('./inside/'+asset['asset_name'])
                node.parm('space').set('2')
                node.parm('shot').set(shot_name)
            except:
                print 'error ', asset['asset_name']
