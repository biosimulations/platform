from typing import Literal

from pydantic import BaseModel, field_validator

# Visibility is the OMEX *resource's* ACL, not a property of the bytes.
OmexVisibility = Literal["public", "private"]


class OmexFile(BaseModel):
    """An OMEX archive resource: a blob plus the ACL of one owner over it.

    ``file_hash_md5`` is the *blob* key (GCS dedupes on it), **not** the resource
    identity. The resource identity is the ``(file_hash_md5, owner_sub)`` pair, so
    two users who upload byte-identical archives get one blob and two independent
    resources -- possession of a hash never grants access to somebody else's
    archive, and a cache hit never rewrites another owner's metadata.
    """

    file_hash_md5: str
    uploaded_filename: str
    bucket_name: str
    omex_gcs_path: str
    file_size: int
    database_id: str | None = None
    # --- ownership & access (stamped server-side from the verified token; never
    # from a request payload) ---
    # None = anonymous ingest (or a legacy row written before ownership existed).
    owner_sub: str | None = None
    # None = legacy row: predates the field, was globally reusable, stays public.
    # See the audit plan's migration strategy (missing visibility -> public).
    visibility: OmexVisibility | None = None

    @property
    def is_public(self) -> bool:
        return self.visibility != "private"

    @field_validator('omex_gcs_path')
    def validate_omex_gcs_path(cls, v: str) -> str:
        if v.find("/") == 0:
            raise ValueError("omex_gcs_path must not be an absolute path")
        return v

    @field_validator('uploaded_filename')
    def validate_uploaded_filename(cls, v: str) -> str:
        if v.find("/") >= 0:
            raise ValueError("uploaded_filename must not contain any path separators")
        return v
