# Changelog

All notable changes to this project will be documented in this file.

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