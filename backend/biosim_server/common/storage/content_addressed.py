"""Create-only publication of content-addressed objects.

A content-addressed name is derived from the bytes it holds, so an object that
already exists at that name should already hold those bytes.  Publication never
overwrites: it creates the object, or reuses the existing one once it has been
checked, or, when the existing object does not match (storage corruption at a
content address), writes a repair copy beside it and logs
``verification_object_integrity_conflict`` for an operator.
"""
import logging
import uuid
from collections.abc import Callable

from biosim_server.common.storage.file_service import FileService

logger = logging.getLogger(__name__)


async def publish_content_addressed(file_service: FileService, contents: bytes, path: str,
                                    matches: Callable[[bytes], bool]) -> str:
    """Publish *contents* at *path* and return the reference to record.

    *matches* decides whether an existing object holds the same content, for
    example by decoding it and comparing its canonical SHA-256.  A created
    object is checked by the backend against the checksum its server reports,
    where there is one, instead of being downloaded again.
    """
    if await file_service.put_if_absent(contents, path):
        return path
    existing = await file_service.get_file_contents(path)
    if existing is None:
        # Removed between the create attempt and the read; try to create it once more.
        if await file_service.put_if_absent(contents, path):
            return path
        raise IOError(f"object {path} exists but cannot be read")
    if existing == contents or _matches(matches, existing):
        return path
    repair = f"{path}.repair-{uuid.uuid4().hex}"
    logger.error("verification_object_integrity_conflict path=%s repair=%s", path, repair)
    if not await file_service.put_if_absent(contents, repair):
        raise IOError(f"repair object {repair} already exists")
    return repair


def _matches(matches: Callable[[bytes], bool], existing: bytes) -> bool:
    try:
        return matches(existing)
    except Exception:
        # An object that cannot even be decoded does not hold this content.
        return False
