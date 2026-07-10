"""
Operators for navigating from Atomic Storage rows to datablock users.
"""

import bpy
from bpy.props import StringProperty
from bpy.utils import register_class
from ..utils import compat
from ..utils import storage_nav


class ATOMIC_OT_storage_navigate_pick(bpy.types.Operator):
    """Navigate to a specific object from a storage row."""

    bl_idname = "atomic.storage_navigate_pick"
    bl_label = "Show User"
    bl_options = {"INTERNAL"}

    object_name: StringProperty(name="Object")
    material_name: StringProperty(name="Material", default="")
    action_name: StringProperty(name="Action", default="")
    nav_mode: StringProperty(name="Navigation", default="viewport")

    def execute(self, context):
        target = {
            "object": self.object_name,
            "material": self.material_name,
            "action": self.action_name,
            "nav": self.nav_mode,
        }
        if storage_nav.apply_target(context, target):
            return {"FINISHED"}
        self.report({"WARNING"}, "Could not select '%s' in the current view layer" % self.object_name)
        return {"CANCELLED"}


class ATOMIC_OT_storage_navigate(bpy.types.Operator):
    """Find and show users of a storage-index datablock."""

    bl_idname = "atomic.storage_navigate"
    bl_label = "Show Storage User"
    bl_description = "Select and activate user object(s) from a storage row"

    storage_type: StringProperty(name="Storage Type")
    id_name: StringProperty(name="ID Name")
    owner_object: StringProperty(name="Owner Object", default="")
    owner_scene: StringProperty(name="Owner Scene", default="")
    modifier_name: StringProperty(name="Modifier", default="")

    def invoke(self, context, event):
        self._targets = storage_nav.resolve_targets(
            self.storage_type,
            self.id_name,
            owner_object=self.owner_object,
            owner_scene=self.owner_scene,
            modifier_name=self.modifier_name,
        )
        if not self._targets:
            self.report({"WARNING"}, "No scene users found for '%s'" % self.id_name)
            return {"CANCELLED"}
        if len(self._targets) == 1:
            return self.execute(context)
        return context.window_manager.invoke_popup(self, width=240)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Select user:")
        for target in self._targets:
            op = layout.operator(
                "atomic.storage_navigate_pick",
                text=target["object"],
                icon="OBJECT_DATA",
            )
            op.object_name = target["object"]
            op.material_name = target.get("material", "")
            op.action_name = target.get("action", "")
            op.nav_mode = target.get("nav", "viewport")

    def execute(self, context):
        targets = getattr(self, "_targets", None)
        if targets is None:
            targets = storage_nav.resolve_targets(
                self.storage_type,
                self.id_name,
                owner_object=self.owner_object,
                owner_scene=self.owner_scene,
                modifier_name=self.modifier_name,
            )
        if not targets:
            self.report({"WARNING"}, "No scene users found for '%s'" % self.id_name)
            return {"CANCELLED"}
        if storage_nav.apply_target(context, targets[0]):
            if len(targets) > 1:
                self.report({"INFO"}, "Showing first of %d users" % len(targets))
            return {"FINISHED"}
        self.report({"WARNING"}, "Could not select user in the current view layer")
        return {"CANCELLED"}


reg_list = (
    ATOMIC_OT_storage_navigate,
    ATOMIC_OT_storage_navigate_pick,
)


def register():
    for item in reg_list:
        register_class(item)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)
