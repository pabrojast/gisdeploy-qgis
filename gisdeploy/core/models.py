"""Plain dataclasses mirroring the GISDeploy agent API views."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _str_or_none(value) -> Optional[str]:
    return value if isinstance(value, str) else None


@dataclass
class Project:
    id: str
    name: str
    slug: str

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(id=data.get("id", ""), name=data.get("name", ""), slug=data.get("slug", ""))


@dataclass
class Revision:
    number: int
    message: Optional[str]
    status: str
    content_hash: Optional[str]
    file_size: int
    original_filename: str
    created_by: Optional[str]
    error_message: Optional[str]
    created_at: str

    @classmethod
    def from_dict(cls, data: dict) -> "Revision":
        return cls(
            number=int(data.get("number", 0)),
            message=_str_or_none(data.get("message")),
            status=data.get("status", ""),
            content_hash=_str_or_none(data.get("contentHash")),
            file_size=int(data.get("fileSize", 0)),
            original_filename=data.get("originalFilename", ""),
            created_by=_str_or_none(data.get("createdByLabel")),
            error_message=_str_or_none(data.get("errorMessage")),
            created_at=data.get("createdAt", ""),
        )


@dataclass
class Dataset:
    id: str
    name: str
    status: str
    dataset_type: str
    geometry_type: Optional[str]
    visibility: str
    project_id: str
    wms_url: Optional[str]
    wmts_url: Optional[str]
    head: Optional[Revision] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Dataset":
        head = data.get("head")
        head_revision = None
        if isinstance(head, dict):
            head_revision = Revision(
                number=int(head.get("number", 0)),
                message=_str_or_none(head.get("message")),
                status="ACTIVE",
                content_hash=_str_or_none(head.get("contentHash")),
                file_size=0,
                original_filename="",
                created_by=None,
                error_message=None,
                created_at=head.get("createdAt", ""),
            )
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
            dataset_type=data.get("datasetType", ""),
            geometry_type=_str_or_none(data.get("geometryType")),
            visibility=data.get("visibility", "PRIVATE"),
            project_id=data.get("projectId", ""),
            wms_url=_str_or_none(data.get("wmsUrl")),
            wmts_url=_str_or_none(data.get("wmtsUrl")),
            head=head_revision,
        )


@dataclass
class DatasetStatus:
    id: str
    name: str
    status: str
    stage: Optional[str]
    error_message: Optional[str]
    dataset_type: str
    wms_url: Optional[str]
    wmts_url: Optional[str]

    @classmethod
    def from_dict(cls, data: dict) -> "DatasetStatus":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
            stage=_str_or_none(data.get("stage")),
            error_message=_str_or_none(data.get("errorMessage")),
            dataset_type=data.get("datasetType", ""),
            wms_url=_str_or_none(data.get("wmsUrl")),
            wmts_url=_str_or_none(data.get("wmtsUrl")),
        )


@dataclass
class Context:
    organization_name: str
    organization_slug: str
    plan: str
    projects: List[Project] = field(default_factory=list)
    usage: Dict[str, object] = field(default_factory=dict)
    limits: Dict[str, object] = field(default_factory=dict)
    scopes: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Context":
        organization = data.get("organization") or {}
        capabilities = data.get("capabilities") or {}
        return cls(
            organization_name=organization.get("name", ""),
            organization_slug=organization.get("slug", ""),
            plan=organization.get("plan", ""),
            projects=[Project.from_dict(p) for p in data.get("projects") or []],
            usage=data.get("usage") or {},
            limits=data.get("limits") if isinstance(data.get("limits"), dict) else {},
            scopes=list(capabilities.get("scopes") or []),
        )
