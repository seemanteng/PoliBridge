"""Schema. Every status is a Claim; other records (obligation, task, finding)
point at claims through Dependency so expiry can reopen them (rule F2)."""
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON, Text, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .clock import clock


def _now():
    return clock.now()


class Person(Base):
    __tablename__ = "person"
    id: Mapped[int] = mapped_column(primary_key=True)
    directory_id: Mapped[str] = mapped_column(String, unique=True)  # identity, never email
    name: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String, nullable=True)  # job title shown in the UI
    # owner | manager | bu_head | ciso | risk_owner | senior_mgmt | compliance | legal
    # | cco | reviewer | internal_audit
    role: Mapped[str] = mapped_column(String)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    business_unit_id: Mapped[int | None] = mapped_column(ForeignKey("business_unit.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deliverable: Mapped[bool] = mapped_column(Boolean, default=True)  # simulated mailbox health


class BusinessUnit(Base):
    __tablename__ = "business_unit"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    head_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)


class Asset(Base):
    __tablename__ = "asset"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    internet_facing: Mapped[bool] = mapped_column(Boolean, default=False)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_facing: Mapped[bool] = mapped_column(Boolean, default=False)
    data_sensitivity: Mapped[str] = mapped_column(String, default="internal")  # internal|confidential|restricted
    business_unit_id: Mapped[int | None] = mapped_column(ForeignKey("business_unit.id"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    technical_owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    legacy_contact: Mapped[str | None] = mapped_column(String, nullable=True)  # old list/email, data-quality only
    app_roots: Mapped[list] = mapped_column(JSON, default=list)  # dirs a scan must cover
    in_scanner_targets: Mapped[bool] = mapped_column(Boolean, default=True)
    # component inventory provenance
    inventory_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inventory_method: Mapped[str | None] = mapped_column(String, nullable=True)  # sbom|sca|authenticated_scan
    inventory_scope: Mapped[list] = mapped_column(JSON, default=list)  # path patterns the inventory examined
    ownership_attested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner = relationship("Person", foreign_keys=[owner_id])
    business_unit = relationship("BusinessUnit", foreign_keys=[business_unit_id])
    components = relationship("Component", back_populates="asset", cascade="all, delete-orphan")


class Component(Base):
    __tablename__ = "component"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"))
    name: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    install_path: Mapped[str | None] = mapped_column(String, nullable=True)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    asset = relationship("Asset", back_populates="components")


class DiscoveredAsset(Base):
    """What discovery sources saw; reconciled against the register (B1)."""
    __tablename__ = "discovered_asset"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)  # network|dns|cloud|scanner
    internet_facing: Mapped[bool] = mapped_column(Boolean, default=False)


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # advisory|regulator|policy|contract
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceVersion(Base):
    __tablename__ = "source_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    version: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Advisory(Base):
    __tablename__ = "advisory"
    id: Mapped[int] = mapped_column(primary_key=True)
    cve: Mapped[str] = mapped_column(String)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    product: Mapped[str] = mapped_column(String)
    affected_versions: Mapped[list] = mapped_column(JSON, default=list)  # ["2.3.5-2.3.31", "2.5-2.5.10"]
    fixed_version: Mapped[str | None] = mapped_column(String, nullable=True)
    cvss: Mapped[float | None] = mapped_column(Float, nullable=True)
    kev: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    is_canary: Mapped[bool] = mapped_column(Boolean, default=False)


class Obligation(Base):
    __tablename__ = "obligation"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # advisory|regulatory|policy
    title: Mapped[str] = mapped_column(String)
    advisory_id: Mapped[int | None] = mapped_column(ForeignKey("advisory.id"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    quotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # candidate -> approved -> open -> closed  (rejected for candidates Legal declines)
    status: Mapped[str] = mapped_column(String, default="open")
    impact: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    applicability_decision: Mapped[str | None] = mapped_column(String, nullable=True)  # human, regulatory
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Claim(Base):
    __tablename__ = "claim"
    id: Mapped[int] = mapped_column(primary_key=True)
    # applicability|remediation|ownership|risk_acceptance|monitoring
    claim_type: Mapped[str] = mapped_column(String)
    statement: Mapped[str] = mapped_column(String)
    value: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. applies|does_not_apply|cannot_determine
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), nullable=True)
    component: Mapped[str | None] = mapped_column(String, nullable=True)
    obligation_id: Mapped[int | None] = mapped_column(ForeignKey("obligation.id"), nullable=True)
    evidence_scope: Mapped[list] = mapped_column(JSON, default=list)
    verification_method: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitter_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)  # who is told on expiry
    expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")  # active|expired|rejected|superseded
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Dependency(Base):
    """dependent_* decisions rely on claim_id; if the claim lapses they reopen (F2)."""
    __tablename__ = "dependency"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"))
    dependent_type: Mapped[str] = mapped_column(String)  # claim|task|risk_acceptance|board_item
    dependent_id: Mapped[int] = mapped_column(Integer)


class Task(Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligation.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"))
    kind: Mapped[str] = mapped_column(String)  # remediation|investigation
    impact: Mapped[str] = mapped_column(String)
    component: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    original_owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    # open|fixed_pending_review|closed|risk_accepted|reopened
    status: Mapped[str] = mapped_column(String, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ack_due: Mapped[datetime] = mapped_column(DateTime)
    interim_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remediate_due: Mapped[datetime] = mapped_column(DateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    interim_control: Mapped[str | None] = mapped_column(Text, nullable=True)
    interim_reopened: Mapped[bool] = mapped_column(Boolean, default=False)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fixed_by_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String, nullable=True)  # remediated|risk_accepted
    board_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    required_reviewers: Mapped[list] = mapped_column(JSON, default=list)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"))
    purpose: Mapped[str] = mapped_column(String)  # remediation|absence
    asset_name: Mapped[str | None] = mapped_column(String, nullable=True)
    component: Mapped[str | None] = mapped_column(String, nullable=True)
    scope: Mapped[list] = mapped_column(JSON, default=list)
    method: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_version: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitter_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|accepted|rejected
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claim.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RiskAcceptance(Base):
    __tablename__ = "risk_acceptance"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"))
    scenario: Mapped[str] = mapped_column(Text)
    controls: Mapped[str] = mapped_column(Text)
    residual_risk: Mapped[str] = mapped_column(String)
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|approved|expired|rejected
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claim.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Notification(Base):
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String)  # assign|escalate_manager|escalate_ciso|deadline_missed|expiry|...
    message: Mapped[str] = mapped_column(Text, default="")
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Finding(Base):
    """Data-quality, coverage, ownership, routing and delivery findings."""
    __tablename__ = "finding"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="Medium")
    subject: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text, default="")
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")  # open|resolved
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Guidance(Base):
    __tablename__ = "guidance"
    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligation.id"))
    role: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft|withheld
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    actor: Mapped[str] = mapped_column(String)
    entity: Mapped[str] = mapped_column(String)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class CanaryRun(Base):
    __tablename__ = "canary_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running|passed|failed
    failures: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BoardItem(Base):
    """A line in the next board risk report. Reopens when a claim it rests on lapses (F2)."""
    __tablename__ = "board_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")  # open|reopened
