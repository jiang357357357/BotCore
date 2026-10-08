"""Compatibility import; legacy local allow/deny configuration is ignored."""

from .backend_policy import is_allowed_by_backend as is_allowed_by_local_policy
