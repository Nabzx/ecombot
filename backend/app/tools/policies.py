"""Policy read-only tools: fetch the active version and validate versions for conflict.

Deterministic only — no semantic retrieval (that is S3).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.repositories.policy import PolicyRepository
from app.rules.enums import RiskLevel
from app.rules.models import PolicyEvidence, RuleResult
from app.rules.policies import PolicyVersionFact, validate_policy_versions
from app.tools.context import ToolContext
from app.tools.enums import Permission, ToolErrorCode
from app.tools.errors import ToolFailure, not_found
from app.tools.registry import RetryPolicy, ToolDefinition
from app.tools.schemas import ActivePolicyResult


class GetActivePolicyInput(BaseModel):
    topic: str


class ValidatePolicyInput(BaseModel):
    topic: str


async def get_active_policy(
    ctx: ToolContext, params: GetActivePolicyInput
) -> ActivePolicyResult:
    repo = PolicyRepository(ctx.require_session())
    version = await repo.get_active_version_for_date(params.topic, ctx.clock.today())
    if version is None:
        raise ToolFailure(
            ToolErrorCode.policy_not_found,
            f"No active policy for topic '{params.topic}'.",
        )
    evidence = PolicyEvidence(
        policy_id=version.policy_id,
        policy_version_id=version.id,
        topic=params.topic,
        version=version.version,
        status=version.status,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
    )
    return ActivePolicyResult(topic=params.topic, evidence=evidence)


async def validate_policy(ctx: ToolContext, params: ValidatePolicyInput) -> RuleResult:
    repo = PolicyRepository(ctx.require_session())
    versions = await repo.find_versions_by_topic(params.topic)
    if not versions:
        raise not_found(f"No policy exists for topic '{params.topic}'.")
    facts = [
        PolicyVersionFact(
            policy_id=v.policy_id,
            policy_version_id=v.id,
            topic=params.topic,
            version=v.version,
            status=v.status,
            effective_from=v.effective_from,
            effective_to=v.effective_to,
        )
        for v in versions
    ]
    return validate_policy_versions(params.topic, facts, ctx.clock)


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_active_policy",
        description="Fetch the active policy version for a topic on the current date.",
        input_model=GetActivePolicyInput,
        output_model=ActivePolicyResult,
        permission=Permission.policy_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="get_active_policy-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=get_active_policy,
    ),
    ToolDefinition(
        name="validate_policy_versions",
        description="Validate a topic's policy versions and detect conflicts.",
        input_model=ValidatePolicyInput,
        output_model=RuleResult,
        permission=Permission.policy_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="validate_policy_versions-v1",
        model_accessible=False,
        retry_policy=RetryPolicy(max_retries=1),
        handler=validate_policy,
    ),
)
