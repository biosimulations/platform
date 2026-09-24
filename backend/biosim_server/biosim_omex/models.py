from typing import Literal

from pydantic import BaseModel, field_validator


class OmexFile(BaseModel):
    file_hash_md5: str
    uploaded_filename: str
    bucket_name: str
    omex_gcs_path: str
    file_size: int
    database_id: str | None = None
    # Optional ownership for newly-created records.  Missing and null remain
    # valid for legacy/shared OMEX records; no migration is performed.
    owner: str | None = None
    # Binary public/private. Missing or null on legacy documents is treated as
    # public by authorization. Never client-supplied.
    visibility: Literal["public", "private"] | None = None

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
