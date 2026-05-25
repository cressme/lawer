"""Compatibility shim for environments that install sqlcipher3 instead."""

from . import dbapi2

__all__ = ["dbapi2"]
