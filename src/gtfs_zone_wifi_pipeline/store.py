"""Object writes with HTTP headers where the store keeps them."""

from __future__ import annotations

from typing import TYPE_CHECKING

import obstore
from obstore.store import S3Store

if TYPE_CHECKING:
    from obstore.store import ObjectStore


def put(
    store: ObjectStore,
    key: str,
    data: bytes,
    content_type: str,
    cache_control: str | None = None,
) -> None:
    """Writes key; Content-Type and Cache-Control are set on S3 only, since
    LocalStore rejects attributes."""
    attributes = None
    if isinstance(store, S3Store):
        attributes = {"Content-Type": content_type}
        if cache_control:
            attributes["Cache-Control"] = cache_control
    obstore.put(store, key, data, attributes=attributes)
