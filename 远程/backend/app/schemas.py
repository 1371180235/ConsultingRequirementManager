from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import DASHBOARD_COMPONENT_KEYS, REQUIREMENT_STATES, ROLE_VALUES


def finite_nonnegative(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("金额必须是大于等于 0 的有限数值")
    return result


def finite_value(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("数值必须是有限数值")
    return result


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class UserCreate(StrictModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    full_name: str = Field(min_length=1, max_length=100)
    role: str
    initial_password: str = Field(min_length=10, max_length=128)
    project_ids: list[int] = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        if value not in ROLE_VALUES:
            raise ValueError("无效角色")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().casefold()


class UserPatch(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = None
    is_active: bool | None = None
    project_ids: list[int] | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str | None) -> str | None:
        if value is not None and value not in ROLE_VALUES:
            raise ValueError("无效角色")
        return value


class PasswordResetIn(StrictModel):
    new_password: str = Field(min_length=10, max_length=128)


class DashboardLayoutPatch(StrictModel):
    component_keys: list[str] = Field(
        min_length=1, max_length=len(DASHBOARD_COMPONENT_KEYS)
    )

    @field_validator("component_keys")
    @classmethod
    def valid_component_keys(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("仪表盘组件不能重复")
        invalid = set(value) - set(DASHBOARD_COMPONENT_KEYS)
        if invalid:
            raise ValueError("包含无效仪表盘组件")
        return value


class ProjectCreate(StrictModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    total_budget: Decimal = Field(default=0, ge=0)
    status: str = Field(default="active", max_length=30)
    current_stage: int = Field(default=1, ge=1, le=6)

    @field_validator("total_budget")
    @classmethod
    def valid_total_budget(cls, value: Decimal) -> Decimal:
        return finite_nonnegative(value)  # type: ignore[return-value]


class ProjectPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    total_budget: Decimal | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=30)
    current_stage: int | None = Field(default=None, ge=1, le=6)

    @field_validator("total_budget")
    @classmethod
    def valid_total_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)


class PlanCreate(StrictModel):
    project_id: int
    year: int = Field(ge=2000, le=2100)
    name: str = Field(min_length=1, max_length=200)
    target: str = Field(default="", max_length=10000)
    budget: Decimal = Field(default=0, ge=0)
    pain_points: str = Field(default="", max_length=10000)

    @field_validator("budget")
    @classmethod
    def valid_budget(cls, value: Decimal) -> Decimal:
        return finite_nonnegative(value)  # type: ignore[return-value]


class PlanPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target: str | None = Field(default=None, max_length=10000)
    budget: Decimal | None = Field(default=None, ge=0)
    pain_points: str | None = Field(default=None, max_length=10000)

    @field_validator("budget")
    @classmethod
    def valid_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)


class VersionCreate(StrictModel):
    annual_plan_id: int
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    target: str = Field(default="", max_length=10000)
    budget: Decimal = Field(default=0, ge=0)

    @field_validator("budget")
    @classmethod
    def valid_budget(cls, value: Decimal) -> Decimal:
        return finite_nonnegative(value)  # type: ignore[return-value]


class VersionPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target: str | None = Field(default=None, max_length=10000)
    budget: Decimal | None = Field(default=None, ge=0)

    @field_validator("budget")
    @classmethod
    def valid_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)


class TagCreate(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = Field(default="#64748B", pattern=r"^#[0-9A-Fa-f]{6}$")


class RequirementCreate(StrictModel):
    code: str = Field(min_length=1, max_length=64)
    stable_key: str | None = Field(default=None, min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    project_id: int
    annual_plan_id: int | None = None
    version_id: int | None = None
    stakeholder_role: str
    estimated_budget: Decimal = Field(default=0, ge=0)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    source_requirement_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)

    @field_validator("stakeholder_role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        if value not in ROLE_VALUES:
            raise ValueError("无效对接角色")
        return value

    @field_validator("estimated_budget")
    @classmethod
    def valid_estimated_budget(cls, value: Decimal) -> Decimal:
        return finite_nonnegative(value)  # type: ignore[return-value]


class RequirementPatch(StrictModel):
    stable_key: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    version_id: int | None = None
    stakeholder_role: str | None = None
    estimated_budget: Decimal | None = Field(default=None, ge=0)
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    source_requirement_id: int | None = None
    tag_ids: list[int] | None = None

    @field_validator("estimated_budget")
    @classmethod
    def valid_estimated_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("需求修改至少需要一个字段")
        for field in ("stable_key", "title", "description", "stakeholder_role", "estimated_budget", "priority", "tag_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class TransitionIn(StrictModel):
    status: str
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in REQUIREMENT_STATES:
            raise ValueError("无效状态")
        return value


class WorkHoursIn(StrictModel):
    estimated_hours: Decimal | None = Field(default=None, ge=0)
    actual_hours: Decimal | None = Field(default=None, ge=0)

    @field_validator("estimated_hours", "actual_hours")
    @classmethod
    def valid_hours(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)


class VersionChangeFields(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target: str | None = Field(default=None, max_length=10000)
    budget: Decimal | None = Field(default=None, ge=0)

    @field_validator("budget")
    @classmethod
    def valid_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("版本变更至少需要一个字段")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class RequirementChangeAddData(StrictModel):
    code: str = Field(min_length=1, max_length=64)
    stable_key: str | None = Field(default=None, min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    stakeholder_role: Literal["admin", "customer", "sales", "manager", "developer", "operator", "leader"]
    estimated_budget: Decimal = Field(default=0, ge=0)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    source_requirement_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)

    @field_validator("estimated_budget")
    @classmethod
    def valid_estimated_budget(cls, value: Decimal) -> Decimal:
        return finite_nonnegative(value)  # type: ignore[return-value]


class RequirementChangeFields(StrictModel):
    stable_key: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    stakeholder_role: Literal["admin", "customer", "sales", "manager", "developer", "operator", "leader"] | None = None
    estimated_budget: Decimal | None = Field(default=None, ge=0)
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    source_requirement_id: int | None = None
    tag_ids: list[int] | None = None

    @field_validator("estimated_budget")
    @classmethod
    def valid_estimated_budget(cls, value: Decimal | None) -> Decimal | None:
        return finite_nonnegative(value)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("需求更新至少需要一个字段")
        for field in ("stable_key", "title", "description", "stakeholder_role", "estimated_budget", "priority", "tag_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class RequirementChangeAdd(StrictModel):
    action: Literal["add"]
    data: RequirementChangeAddData


class RequirementChangeUpdate(StrictModel):
    action: Literal["update"]
    requirement_id: int
    fields: RequirementChangeFields


class RequirementChangeRemove(StrictModel):
    action: Literal["remove"]
    requirement_id: int


RequirementChangeOperation = Annotated[
    RequirementChangeAdd | RequirementChangeUpdate | RequirementChangeRemove,
    Field(discriminator="action"),
]


class ArtifactChangeAddData(StrictModel):
    stage: Literal[3, 4, 5, 6]
    category: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    requirement_id: int | None = None
    upload_token: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{32}$"
    )

    @model_validator(mode="after")
    def validate_scope(self):
        if self.stage == 6 and self.requirement_id is None:
            raise ValueError("运维成果物必须关联当前版本内的需求")
        if self.stage != 6 and self.requirement_id is not None:
            raise ValueError("建设、招投标和验收成果物不能关联需求")
        return self


class ArtifactChangeFields(StrictModel):
    category: str | None = Field(default=None, min_length=1, max_length=50)
    title: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("成果物更新至少需要一个字段")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ArtifactChangeAdd(StrictModel):
    action: Literal["add"]
    data: ArtifactChangeAddData


class ArtifactChangeUpdate(StrictModel):
    action: Literal["update"]
    artifact_id: int
    fields: ArtifactChangeFields


class ArtifactChangeSubmit(StrictModel):
    action: Literal["submit"]
    artifact_id: int


class ArtifactChangeDecision(StrictModel):
    action: Literal["decide"]
    artifact_id: int
    approved: bool
    note: str = Field(default="", max_length=10000)


class ArtifactChangeReplaceFile(StrictModel):
    action: Literal["replace_file"]
    artifact_id: int
    upload_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class ArtifactChangeRemove(StrictModel):
    action: Literal["remove"]
    artifact_id: int


ArtifactChangeOperation = Annotated[
    ArtifactChangeAdd
    | ArtifactChangeUpdate
    | ArtifactChangeSubmit
    | ArtifactChangeDecision
    | ArtifactChangeReplaceFile
    | ArtifactChangeRemove,
    Field(discriminator="action"),
]


class ChangePayload(StrictModel):
    version: VersionChangeFields | None = None
    requirements: list[RequirementChangeOperation] = Field(default_factory=list)
    artifacts: list[ArtifactChangeOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_operation(self):
        if self.version is None and not self.requirements and not self.artifacts:
            raise ValueError("变更内容必须包含版本字段、需求操作或成果物操作")
        return self


class ChangeCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=10000)
    change_type: str = Field(min_length=1, max_length=40)
    payload: ChangePayload


class ChangeRequestCreate(ChangeCreate):
    version_id: int


class DecisionIn(StrictModel):
    approved: bool
    note: str = Field(default="", max_length=10000)


class BudgetEntryCreate(StrictModel):
    project_id: int
    annual_plan_id: int | None = None
    version_id: int | None = None
    requirement_id: int | None = None
    entry_type: Literal["allocation", "actual", "adjustment"]
    amount: Decimal
    description: str = Field(default="", max_length=500)
    allow_actual_overrun: bool = False

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal) -> Decimal:
        return finite_value(value)  # type: ignore[return-value]


class FundingApplicationCreate(StrictModel):
    project_id: int
    annual_plan_id: int
    version_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    amount: Decimal = Field(gt=0)
    note: str = Field(default="", max_length=10000)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal) -> Decimal:
        value = finite_nonnegative(value)  # type: ignore[assignment]
        if value is None or value <= 0:
            raise ValueError("申报金额必须大于 0")
        return value


class FundingApplicationPatch(StrictModel):
    annual_plan_id: int | None = None
    version_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    amount: Decimal | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=10000)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        value = finite_nonnegative(value)
        if value is None or value <= 0:
            raise ValueError("申报金额必须大于 0")
        return value


class FundingStatusIn(StrictModel):
    status: Literal["draft", "submitted", "reviewing", "approved", "rejected", "disbursed"]


class ArtifactCreate(StrictModel):
    project_id: int
    annual_plan_id: int | None = None
    version_id: int | None = None
    requirement_id: int | None = None
    stage: int = Field(ge=1, le=6)
    category: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)


class ArtifactDecisionIn(StrictModel):
    approved: bool
    note: str = Field(default="", max_length=10000)


class OperationCreate(StrictModel):
    project_id: int
    version_id: int | None = None
    requirement_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20000)
    feedback_type: Literal["issue", "bug", "promotion", "question", "improvement"] = "issue"


class OperationPatch(StrictModel):
    status: Literal["open", "processing", "resolved", "closed"]


class HealthOut(StrictModel):
    status: str
    database: str
    timestamp: datetime
