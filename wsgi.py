"""WSGI entrypoint for deployment platforms that auto-detect Flask apps.

The Flask instance is defined in the root-level api.py module.
"""
from api import app

__all__ = ["app"]
