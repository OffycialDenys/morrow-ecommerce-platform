"""Static-asset collection settings used while building the production image."""

from .settings import *  # noqa: F403

STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
