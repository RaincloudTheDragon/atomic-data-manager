import bpy  # type: ignore

RAINYS_EXTENSIONS_REPO_NAME = "Rainy's Extensions"
RAINYS_EXTENSIONS_REPO_URL = (
    "https://raw.githubusercontent.com/RaincloudTheDragon/rainys-blender-extensions/refs/heads/main/index.json"
)

_BOOTSTRAP_DONE = False


def _log(message: str) -> None:
    print(f"RainysExtensionsCheck: {message}")


def ensure_rainys_extensions_repo(_deferred: bool = False) -> None:
    """
    Ensure the Rainy's Extensions repository is registered in Blender.

    Safe to import and call from multiple add-ons; the helper guards against doing the
    work more than once per Blender session.
    """
    global _BOOTSTRAP_DONE

    if _BOOTSTRAP_DONE:
        return

    _log("starting repository verification")

    context_class_name = type(bpy.context).__name__
    if context_class_name == "_RestrictContext":
        if _deferred:
            _log("context still restricted after deferral; aborting repo check")
            return

        _log("context restricted; scheduling repo check retry")

        def _retry():
            ensure_rainys_extensions_repo(_deferred=True)
            return None

        bpy.app.timers.register(_retry, first_interval=0.5)
        return

    prefs = getattr(bpy.context, "preferences", None)
    if prefs is None:
        _log("no preferences available on context; skipping")
        return

    preferences_changed = False
    addon_prefs = None
    addon_entry = None
    if hasattr(getattr(prefs, "addons", None), "get"):
        addon_entry = prefs.addons.get(__name__)
    elif hasattr(prefs, "addons"):
        try:
            addon_entry = prefs.addons[__name__]
        except Exception:
            addon_entry = None
    if addon_entry:
        addon_prefs = getattr(addon_entry, "preferences", None)
    addon_repo_initialized = bool(
        addon_prefs and getattr(addon_prefs, "repo_initialized", False)
    )

    experimental = getattr(prefs, "experimental", None)
    if experimental and hasattr(experimental, "use_extension_platform"):
        if not experimental.use_extension_platform:
            experimental.use_extension_platform = True
            preferences_changed = True
            _log("enabled experimental extension platform")

    repositories = None
    extensions_obj = getattr(prefs, "extensions", None)
    if extensions_obj:
        if hasattr(extensions_obj, "repos"):
            repositories = extensions_obj.repos
        elif hasattr(extensions_obj, "repositories"):
            repositories = extensions_obj.repositories

    if repositories is None:
        filepaths = getattr(prefs, "filepaths", None)
        repositories = getattr(filepaths, "extension_repos", None) if filepaths else None

    if repositories is None:
        _log("extension repositories collection missing; skipping")
        return

    def _repo_matches(repo) -> bool:
        return getattr(repo, "remote_url", "") == RAINYS_EXTENSIONS_REPO_URL or getattr(
            repo, "url", ""
        ) == RAINYS_EXTENSIONS_REPO_URL

    matching_indices = [idx for idx, repo in enumerate(repositories) if _repo_matches(repo)]

    target_repo = None
    if matching_indices:
        target_repo = repositories[matching_indices[0]]
        if len(matching_indices) > 1 and hasattr(repositories, "remove"):
            for dup_idx in reversed(matching_indices[1:]):
                try:
                    repositories.remove(dup_idx)
                    _log(f"removed duplicate repository entry at index {dup_idx}")
                except Exception as exc:
                    _log(f"could not remove duplicate repository at index {dup_idx}: {exc}")
    else:
        target_repo = next(
            (
                repo
                for repo in repositories
                if getattr(repo, "name", "") == RAINYS_EXTENSIONS_REPO_NAME
            ),
            None,
        )

    if target_repo is None:
        _log("repo missing; creating new entry")
        if hasattr(repositories, "new"):
            target_repo = repositories.new()
        elif hasattr(repositories, "add"):
            target_repo = repositories.add()
        else:
            _log("repository collection does not support creation; aborting")
            return
    else:
        _log("repo entry already present; validating fields")

    changed = preferences_changed

    def _ensure_attr(obj, attr, value):
        if hasattr(obj, attr) and getattr(obj, attr) != value:
            setattr(obj, attr, value)
            return True
        if not hasattr(obj, attr):
            _log(f"repository entry missing attribute '{attr}', skipping field")
        return False

    changed |= _ensure_attr(target_repo, "name", RAINYS_EXTENSIONS_REPO_NAME)
    changed |= _ensure_attr(target_repo, "module", "rainys_extensions")
    changed |= _ensure_attr(target_repo, "use_remote_url", True)
    changed |= _ensure_attr(target_repo, "remote_url", RAINYS_EXTENSIONS_REPO_URL)
    changed |= _ensure_attr(target_repo, "use_sync_on_startup", True)
    changed |= _ensure_attr(target_repo, "use_cache", True)
    changed |= _ensure_attr(target_repo, "use_access_token", False)

    if addon_prefs and hasattr(addon_prefs, "repo_initialized") and not addon_prefs.repo_initialized:
        addon_prefs.repo_initialized = True
        changed = True

    if not changed:
        _log("repository already configured; skipping preference save")
        _BOOTSTRAP_DONE = True
        return

    if hasattr(bpy.ops, "wm") and hasattr(bpy.ops.wm, "save_userpref"):
        try:
            bpy.ops.wm.save_userpref()
            _log("preferences updated and saved")
        except Exception as exc:  # pragma: no cover
            print(f"RainysExtensionsCheck: could not save preferences after repo update -> {exc}")
    else:
        _log("preferences API unavailable; changes not persisted")

    _BOOTSTRAP_DONE = True


def register() -> None:
    """Entry point for Blender add-on registration."""
    ensure_rainys_extensions_repo()


def unregister() -> None:
    """Reset bootstrap guard so next registration re-runs the checks."""
    global _BOOTSTRAP_DONE
    _BOOTSTRAP_DONE = False
