## [v2.2.0] - 2026-01-05

### Features
- Add loading bars; non-blocking timer-based UI (#10)
  - Operations no longer freeze the UI during scanning
  - Real-time progress updates with cancel support at any time
  - Descriptive status messages showing current operation details
- Unified Smart Select and Clean scanning logic
  - Eliminated code duplication between operations
  - Clean now only scans selected categories (more efficient)
  - Both operations use consistent incremental scanning for images and worlds
- Added manual cache clear operator for testing and debugging

### Performance
- Optimized deep scan functions with caching and fast-path checks
  - Image scanning now uses cached results to avoid redundant scene scans
  - Early exit for clearly unused images using Blender's built-in user count
- Incremental processing for large datasets
  - Images processed in batches (5 per callback) to maintain UI responsiveness
  - Worlds processed one at a time incrementally

### Fixes
- Fixed images used only by unused objects being incorrectly flagged as unused (#5)
- Fixed material detection in brushes and node groups (#6, #7)
- Fixed Clean operator not showing dialog when invoked programmatically (#8)
- Improved material detection in inspection tools (brushes, node groups)

### Internal
- Refactored scanning architecture for maintainability
- Added comprehensive debug output for troubleshooting

## [v2.1.0] - 2025-12-18

### Features
- Added support for detecting unused objects and armatures (#1)
  - Objects not present in any scene collections are now detected as unused
  - Armatures not used by any objects in scenes (including direct use, modifiers, and constraints like "Child Of") are detected as unused
  - Smart Select and Clean operations now support objects and armatures

### Fixes
- Fixed material detection in Geometry Nodes Set Material nodes
  - Materials used in Geometry Nodes' "Set Material" nodes are now correctly detected as used
  - Fixed legacy issue where materials in node groups (e.g., "outline-highlight" in "box-highlight" node group) were incorrectly flagged as unused
  - This was a hangover from Atomic never being developed past Blender 2.93, where Geometry Nodes Set Material nodes use input sockets rather than direct material properties
- Performance optimizations for Smart Select and Clean operations (#3)
  - Removed inefficient threading implementation that was causing poor performance
  - Implemented short-circuiting logic in Smart Select to exit early when unused items are found
  - Fixed UI operators to use cached values instead of recalculating on every draw call
  - Note: Further performance improvements are limited by Blender's Python API being single-threaded and requiring sequential access to `bpy.data` collections, making true parallelization impossible without risking data corruption

### Internal
- Removed incorrect "Remington Creative" copyright notices from newly created files
- Updated repository configuration in manifest

## [v2.0.3] - 2025-12-17

### Fixes
- Fixed missing import error in missing file detection

## [v2.0.2] - 2025-12-17

### Fixes
- Atomic now completely ignores all library-linked and override datablocks across all operations, as originally intended.

## [v2.0.1] - 2025-12-16

### Fixes
- Blender 5.0 compatibility: Fixed `AttributeError` when detecting missing library files (Library objects use `packed_file` singular, Image objects use `packed_files` plural in 5.0)
- Fixed unregistration errors in Blender 4.5 by using safe unregister functions throughout the codebase

## [v2.0.0] - Raincloud's first re-release

### Feature
- Multi-version Blender support (4.2 LTS, 4.5 LTS, and 5.0)
    - Version detection utilities in `utils/version.py`
    - API compatibility layer in `utils/compat.py` for handling version differences

### Fixes
- Blender 5.0 compatibility: Fixed `AttributeError` when accessing scene compositor node tree (changed from `scene.node_tree` to `scene.compositing_node_tree`)
- Collections assigned to `rigidbody_world.collection` are now correctly detected as used

### Internal
- GitHub Actions release workflow
- Integrated `rainys_repo_bootstrap` into `__init__.py` so the Rainy's Extensions repository is registered on add-on enable and the bootstrap guard resets on disable.
- Removed "Support Remington Creative" popup and all related functionality
    - Removed Support popup preferences