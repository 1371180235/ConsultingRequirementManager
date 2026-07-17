from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    # MySQL DATETIME is timezone-naive; persist normalized UTC consistently.
    return datetime.now(UTC).replace(tzinfo=None)


ROLE_VALUES = ("admin", "customer", "sales", "manager", "developer", "operator", "leader")
DASHBOARD_COMPONENT_KEYS = (
    "metrics",
    "status_distribution",
    "budget_distribution",
    "recent_requirements",
    "tasks",
)
DEFAULT_DASHBOARD_LAYOUTS = {
    "admin": DASHBOARD_COMPONENT_KEYS,
    "customer": ("metrics", "recent_requirements", "tasks"),
    "sales": ("metrics", "budget_distribution", "recent_requirements", "tasks"),
    "manager": DASHBOARD_COMPONENT_KEYS,
    "developer": ("metrics", "status_distribution", "tasks", "recent_requirements"),
    "operator": ("metrics", "tasks", "status_distribution"),
    "leader": DASHBOARD_COMPONENT_KEYS,
}
REQUIREMENT_STATES = (
    "draft",
    "planning",
    "scheduled",
    "developing",
    "acceptance",
    "online",
    "closed",
    "rejected",
    "suspended",
    "cancelled",
    "changing",
    "returned",
)
REQUIREMENT_TRANSITIONS = {
    "draft": ("planning", "cancelled"),
    "planning": ("scheduled", "rejected", "suspended", "returned"),
    "scheduled": ("developing", "suspended", "cancelled", "returned"),
    "developing": ("acceptance", "suspended", "returned"),
    "acceptance": ("online", "returned"),
    "online": ("closed", "changing"),
    "closed": ("changing",),
    "rejected": ("draft", "cancelled"),
    "suspended": ("planning", "scheduled", "developing", "cancelled"),
    "cancelled": ("draft",),
    "changing": ("planning", "scheduled", "developing", "acceptance"),
    "returned": ("planning", "scheduled", "developing", "cancelled"),
}
VERSION_STATES = ("draft", "frozen", "released")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class RoleDashboardLayout(Base):
    __tablename__ = "role_dashboard_layouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    component_keys: Mapped[list[str]] = mapped_column(JSON)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    total_budget: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="active")
    current_stage: Mapped[int] = mapped_column(Integer, default=1)


class ProjectAccess(Base):
    __tablename__ = "project_access"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_user_project_access"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)


class AnnualPlan(Base, TimestampMixin):
    __tablename__ = "annual_plans"
    __table_args__ = (UniqueConstraint("project_id", "year", name="uq_project_year"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    pain_points: Mapped[str] = mapped_column(Text, default="")


class DeliveryVersion(Base, TimestampMixin):
    __tablename__ = "delivery_versions"
    __table_args__ = (UniqueConstraint("annual_plan_id", "code", name="uq_plan_version_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    annual_plan_id: Mapped[int] = mapped_column(ForeignKey("annual_plans.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    color: Mapped[str] = mapped_column(String(20), default="#64748B")


class RequirementTag(Base):
    __tablename__ = "requirement_tags"
    __table_args__ = (UniqueConstraint("requirement_id", "tag_id", name="uq_requirement_tag"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirements_scope", "project_id", "annual_plan_id", "version_id"),
        UniqueConstraint("version_id", "stable_key", name="uq_requirement_version_stable_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stable_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    annual_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("annual_plans.id", ondelete="SET NULL"), index=True, nullable=True
    )
    version_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_versions.id", ondelete="SET NULL"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stakeholder_role: Mapped[str] = mapped_column(String(20))
    estimated_budget: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    allocated_budget: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    source_requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    estimated_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    actual_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)


class RequirementStatusHistory(Base):
    __tablename__ = "requirement_status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), index=True)
    transition_note: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class VersionBaseline(Base):
    __tablename__ = "version_baselines"
    __table_args__ = (
        UniqueConstraint("version_id", "sequence", name="uq_version_baseline_sequence"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("delivery_versions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChangeRequest(Base, TimestampMixin):
    __tablename__ = "change_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("delivery_versions.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_baseline_sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    applied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArtifactChangeUpload(Base):
    __tablename__ = "artifact_change_uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_versions.id", ondelete="CASCADE"), index=True
    )
    expected_baseline_sequence: Mapped[int] = mapped_column(Integer)
    change_request_id: Mapped[int] = mapped_column(
        ForeignKey("change_requests.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256_hex: Mapped[str | None] = mapped_column(String(64))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BudgetEntry(Base, TimestampMixin):
    __tablename__ = "budget_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    annual_plan_id: Mapped[int | None] = mapped_column(ForeignKey("annual_plans.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_versions.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    entry_type: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    description: Mapped[str] = mapped_column(String(500), default="")
    occurred_on: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class FundingApplication(Base, TimestampMixin):
    __tablename__ = "funding_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    annual_plan_id: Mapped[int] = mapped_column(ForeignKey("annual_plans.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_versions.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")


class Deliverable(Base, TimestampMixin):
    __tablename__ = "deliverables"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    annual_plan_id: Mapped[int | None] = mapped_column(ForeignKey("annual_plans.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_versions.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    stage: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    content_type: Mapped[str | None] = mapped_column(String(150))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approval_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(Text)


class OperationFeedback(Base, TimestampMixin):
    __tablename__ = "operation_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_versions.id", ondelete="SET NULL"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    feedback_type: Mapped[str] = mapped_column(String(30), default="issue")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    before_data: Mapped[dict | None] = mapped_column(JSON)
    after_data: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
