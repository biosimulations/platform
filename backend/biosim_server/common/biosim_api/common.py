"""Shared building blocks for the biosimulations.org API mirror models.

Every model in this package mirrors an upstream ``api.biosimulations.org``
response. They are **passthrough contracts, not domain models**:

* ``extra="allow"`` keeps unmodeled upstream keys intact, so the proxy never
  strips a field it does not happen to type yet;
* aliases preserve the upstream camelCase wire keys in both directions
  (FastAPI serializes response models ``by_alias=True``);
* every field the upstream may omit is optional with a default.

That last rule is the important one: a proxy that 500s because upstream added
or dropped a key is strictly worse than one that passes the payload through.
"""

from pydantic import BaseModel, ConfigDict, Field


class UpstreamModel(BaseModel):
    """Base for every biosimulations.org response mirror in this package.

    Carries the passthrough config in one place so no individual model can
    forget ``extra="allow"`` and start silently dropping upstream keys.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class LabeledIdentifier(UpstreamModel):
    """``{uri, label}`` object used for creators, keywords, citations, encodes."""

    uri: str | None = None
    label: str | None = None


class LogMessage(UpstreamModel):
    """``{type, message}`` — the shape of *both* ``skipReason`` and ``exception``.

    One model, two field names: the log payload repeats this pair at the run,
    SED-document, task and output levels. ``None`` at a call site means the key
    was absent (i.e. "not skipped" / "did not raise"), which is exactly how the
    frontend reads it — so never default this to an empty instance.
    """

    # `type` is a builtin; the field is `type_` and the alias carries the wire key.
    type_: str | None = Field(default=None, alias="type")
    message: str | None = None
