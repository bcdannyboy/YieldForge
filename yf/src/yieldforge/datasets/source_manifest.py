"""Strict contracts for pinned external dataset releases."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from yieldforge.domain import ContractModel


class SourceFile(ContractModel):
    """One immutable, checksummed file in a published dataset release."""

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    checksum_algorithm: Literal["md5"]
    checksum: str = Field(pattern=r"^[0-9a-f]{32}$")

    @field_validator("name")
    @classmethod
    def require_plain_basename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("source file name must be a plain basename")
        return value


class DatasetSourceManifest(ContractModel):
    """Pinned identity and files for one reproducible dataset source."""

    schema_version: Literal["yieldforge.dataset-source.v1"]
    dataset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doi: str = Field(min_length=1)
    version: str = Field(min_length=1)
    license: str = Field(min_length=1)
    source_page: str = Field(min_length=1)
    files: tuple[SourceFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_file_names(self) -> Self:
        names = [file.name for file in self.files]
        if len(names) != len(set(names)):
            raise ValueError("source file names must be unique")
        return self
