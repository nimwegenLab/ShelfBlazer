"""ShelfBlazer web app (FastAPI pages and command line). Core logic lives in the shelfblazer package."""
from importlib.metadata import PackageNotFoundError, version as _version

# The product version is the one in app/pyproject.toml (packages/pyproject.toml carries the same number; a test checks it).
try:
    __version__ = _version("shelfblazer-app")
except PackageNotFoundError:        # a source tree that was never installed
    __version__ = "0+unknown"
