"""Preview plugins.

Every module in this package (and in any directory listed in the
``S3FB_PLUGIN_DIRS`` environment variable) whose name does not start with an
underscore is a preview plugin. A plugin is a plain Python module with:

``can_preview(filename, content_type) -> bool``  (required)
    Return True if the plugin knows how to show this object. ``filename`` is
    the full object key, ``content_type`` the Content-Type stored in S3 (may
    be empty or ``application/octet-stream``).

``render_preview(fileobj, filename, content_type, bucket_name=None) -> str | None``  (required)
    Return an HTML fragment that the UI inserts into the preview dialog, or
    None to let the next plugin try. ``fileobj`` is a file-like object with
    the object's bytes (see ``MAX_BYTES``). Escape everything that comes from
    the object or its key (``preview_plugins._util.esc``).

``NAME``      (optional, str)  Display name; defaults to the module name.
``PRIORITY``  (optional, int)  Lower runs first. Default 50. The generic text
              plugin uses 90 and the fallback 100, so specific plugins win.
``MAX_BYTES`` (optional, int or None)
              ``None`` (default): the whole object is passed in.
              ``0``: no content is downloaded; ``fileobj`` is None (for
              plugins that only build a URL, e.g. PDF, video, audio).
              ``N``: only the first N bytes are downloaded (HTTP Range).

Objects larger than ``S3FB_PREVIEW_MAX_MB`` are not handed to plugins that
need the whole object.
"""
import importlib
import importlib.util
import logging
import os
import pkgutil
import sys

logger = logging.getLogger(__name__)

_PLUGINS = None


def _load_from_dir(directory: str):
    loaded = []
    for info in pkgutil.iter_modules([directory]):
        if info.name.startswith('_'):
            continue
        path = os.path.join(directory, info.name + '.py')
        if not os.path.isfile(path):
            continue
        mod_name = f's3fb_external_plugin_{info.name}'
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
        except Exception as e:  # a broken plugin must not take the app down
            logger.error(f"Could not load preview plugin {path}: {e}")
    return loaded


def _valid(module) -> bool:
    ok = callable(getattr(module, 'can_preview', None)) and callable(getattr(module, 'render_preview', None))
    if not ok:
        logger.error(f"Ignoring preview plugin {module.__name__}: it must define "
                      f"can_preview() and render_preview()")
    return ok


def get_plugins():
    """Return the plugin modules, sorted by PRIORITY (cached)."""
    global _PLUGINS
    if _PLUGINS is not None:
        return _PLUGINS

    modules = []
    here = os.path.dirname(os.path.abspath(__file__))
    for info in pkgutil.iter_modules([here]):
        if info.name.startswith('_'):
            continue
        try:
            modules.append(importlib.import_module(f'{__name__}.{info.name}'))
        except Exception as e:
            logger.error(f"Could not load preview plugin {info.name}: {e}")

    for directory in filter(None, os.environ.get('S3FB_PLUGIN_DIRS', '').split(os.pathsep)):
        if os.path.isdir(directory):
            modules.extend(_load_from_dir(directory))
        else:
            logger.warning(f"S3FB_PLUGIN_DIRS entry is not a directory: {directory}")

    modules = [m for m in modules if _valid(m)]
    for m in modules:
        if not hasattr(m, 'NAME'):
            m.NAME = m.__name__.rsplit('.', 1)[-1].replace('s3fb_external_plugin_', '')
    modules.sort(key=lambda m: (getattr(m, 'PRIORITY', 50), m.NAME))
    logger.info("Preview plugins: " + ', '.join(f"{m.NAME}({getattr(m, 'PRIORITY', 50)})" for m in modules))
    _PLUGINS = modules
    return _PLUGINS


def reset_cache():
    """Forget loaded plugins (used by tests)."""
    global _PLUGINS
    _PLUGINS = None
