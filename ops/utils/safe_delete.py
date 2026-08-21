"""
Safe bulk data-block removal helpers.

Heavy scenes can AV in Blender's draw path (drw_batch_cache_validate /
foreach_obref_in_scene) after mass bpy.data.*.remove() when the active
View3D immediately re-syncs. Switching all windows to an empty throwaway
scene for the duration of deletes avoids evaluating the heavy scene mid-purge.
"""

from contextlib import contextmanager

import bpy

from ... import config


# Reserved name; leading dot keeps it out of casual scene pickers
_SAFE_SCENE_NAME = ".AtomicSafeClean"

# Re-entrancy: clean_all nests many category helpers under one switch
_safe_depth = 0
_restore_window_scenes = None


def tag_atomic_ui_redraw():
    """Redraw Properties (Atomic panels) only — not every area / View3D."""
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return
    try:
        for area in screen.areas:
            if area.type == "PROPERTIES":
                area.tag_redraw()
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _ensure_safe_scene():
    """Create or reuse the throwaway empty scene used during Safe Clean."""
    scene = bpy.data.scenes.get(_SAFE_SCENE_NAME)
    if scene is None:
        scene = bpy.data.scenes.new(_SAFE_SCENE_NAME)
    return scene


def _set_windows_scene(scene):
    """Set scene on every window (not just context.scene)."""
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        try:
            window.scene = scene
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue


def _restore_windows(saved):
    """Restore each window to its prior scene datablock if still valid."""
    if not saved:
        return
    for window, scene in saved:
        if window is None or scene is None:
            continue
        try:
            if scene.name not in bpy.data.scenes:
                continue
            window.scene = scene
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue


def _remove_safe_scene_if_unused():
    """Delete the throwaway scene only after no window still has it active."""
    scene = bpy.data.scenes.get(_SAFE_SCENE_NAME)
    if scene is None:
        return
    wm = bpy.context.window_manager
    if wm is not None:
        try:
            if any(w.scene == scene for w in wm.windows):
                return
        except (AttributeError, ReferenceError, RuntimeError):
            return
    try:
        bpy.data.scenes.remove(scene)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _update_active_depsgraph():
    """Flush evaluation on the (empty) active scene before switching back."""
    try:
        view_layer = getattr(bpy.context, "view_layer", None)
        if view_layer is not None:
            view_layer.update()
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        if depsgraph is not None:
            depsgraph.update()
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        pass


def begin_safe_datablock_removal(enabled=None):
    """
    Enter Safe Clean (for multi-tick timers). Pair with end_safe_datablock_removal.

    Returns True if this call performed the scene switch (outermost enter).
    """
    global _safe_depth, _restore_window_scenes

    if enabled is None:
        enabled = bool(getattr(config, "safe_clean_empty_scene", True))
    if not enabled:
        return False

    entering = _safe_depth == 0
    _safe_depth += 1

    if entering:
        wm = bpy.context.window_manager
        windows = list(wm.windows) if wm is not None else []
        _restore_window_scenes = [(w, w.scene) for w in windows]
        safe_scene = _ensure_safe_scene()
        _set_windows_scene(safe_scene)
        config.debug_print(
            "[Atomic Debug] Safe Clean: switched to empty scene "
            f"'{_SAFE_SCENE_NAME}' for datablock removal"
        )
    return entering


def end_safe_datablock_removal(enabled=None):
    """
    Leave Safe Clean (for multi-tick timers). Safe to call if begin was skipped.
    """
    global _safe_depth, _restore_window_scenes

    if enabled is None:
        enabled = bool(getattr(config, "safe_clean_empty_scene", True))
    if not enabled:
        tag_atomic_ui_redraw()
        return

    if _safe_depth <= 0:
        tag_atomic_ui_redraw()
        return

    if _safe_depth == 1:
        _update_active_depsgraph()

    _safe_depth -= 1
    if _safe_depth == 0:
        saved = _restore_window_scenes
        _restore_window_scenes = None
        _restore_windows(saved)
        _remove_safe_scene_if_unused()
        tag_atomic_ui_redraw()
        config.debug_print(
            "[Atomic Debug] Safe Clean: restored prior scenes after removal"
        )


@contextmanager
def safe_datablock_removal(enabled=None):
    """
    Run bulk ID deletes with all windows on an empty scene when Safe Clean is on.

    Nested uses share one enter/exit (re-entrant). Always restores prior window
    scenes in finally, then removes the throwaway scene if unused.

    Args:
        enabled: Override preference. None reads config.safe_clean_empty_scene.
    """
    if enabled is None:
        enabled = bool(getattr(config, "safe_clean_empty_scene", True))

    if not enabled:
        yield
        return

    begin_safe_datablock_removal(enabled=True)
    try:
        yield
    finally:
        end_safe_datablock_removal(enabled=True)
