from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.constants import (
    AGENT_BUILD_CREDIT_SECONDS,
    AGENT_BUILD_RATE_LIMIT,
    AGENT_BUILD_RATE_WINDOW_SECONDS,
)
from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.schemas.agent_builder import AgentBuildRequest, AgentBuildResponse
from api.services.agent_builder import generator
from api.services.agent_builder.service import (
    AgentBuilderError,
    BusinessInfo,
    create_agent_workflow,
    list_templates,
)
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event
from api.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/agent-builder")


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    fields: list[str]


class CreateAgentRequest(BaseModel):
    mode: Literal["describe", "template"]
    description: Optional[str] = None
    template_id: Optional[str] = None
    business: BusinessInfo


class CreateAgentResponse(BaseModel):
    workflow_id: int
    name: str


@router.get("/templates")
async def get_agent_templates(
    user: UserModel = Depends(get_user),
) -> List[AgentTemplateResponse]:
    """List the built-in agent templates (id, name, description, fields)."""
    return [AgentTemplateResponse(**t) for t in list_templates()]


@router.post("/create")
async def create_agent(
    request: CreateAgentRequest,
    user: UserModel = Depends(get_user),
) -> CreateAgentResponse:
    """Create a working voice agent from a description or a template.

    Builds a minimal valid workflow (start → agent → end) owned by the
    caller's organization and returns its id for redirecting to the editor.
    """
    try:
        workflow = await create_agent_workflow(
            mode=request.mode,
            description=request.description,
            template_id=request.template_id,
            business=request.business,
            user_id=user.id,
            organization_id=user.selected_organization_id,
        )
    except AgentBuilderError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Agent builder failed to create workflow: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agent")

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "source": "agent_builder",
            "mode": request.mode,
            "template_id": request.template_id,
            "organization_id": user.selected_organization_id,
        },
    )

    return CreateAgentResponse(workflow_id=workflow.id, name=workflow.name)


@router.post("/generate")
async def generate_agent(
    request: AgentBuildRequest,
    user: UserModel = Depends(get_user),
) -> AgentBuildResponse:
    """Generate a draft workflow (+ tools) from a business prompt via Claude.

    Calls the Anthropic Claude API to design a Dograh workflow_definition,
    creates the http_api tools it needs, and saves the result as a draft
    workflow owned by the caller's organization.

    Metered: rate-limited per org (protects the platform LLM key) and charges a
    flat credit per successful generation (AGENT_BUILD_CREDIT_SECONDS; 0 = free).
    """
    org = user.selected_organization_id

    # Rate-limit per org — the LLM call is expensive on the platform key.
    await enforce_rate_limit(
        bucket="agent_build",
        identity=str(org),
        limit=AGENT_BUILD_RATE_LIMIT,
        window_seconds=AGENT_BUILD_RATE_WINDOW_SECONDS,
    )

    # Pre-check credits so we don't spend LLM tokens for a metered org that
    # can't afford the flat charge (unmetered/NULL-balance orgs skip this).
    if AGENT_BUILD_CREDIT_SECONDS > 0:
        balance = await db_client.get_free_call_seconds_remaining(org)
        if balance is not None and balance < AGENT_BUILD_CREDIT_SECONDS:
            raise HTTPException(status_code=402, detail="insufficient_credits")

    try:
        result = await generator.generate_agent(request, user)
    except generator.AgentBuilderConfigError as e:
        # Missing ANTHROPIC_API_KEY (or anthropic package) -> not configured.
        raise HTTPException(status_code=503, detail=str(e))
    except generator.AgentBuilderClaudeError as e:
        # Claude request or JSON-parse failure.
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - never leak a raw 500
        logger.error(f"Agent builder generate failed: {e}")
        raise HTTPException(
            status_code=502, detail=f"Agent generation failed: {e}"
        )

    # Charge the flat credit for this successful generation. Idempotent per
    # created workflow so a client retry can't double-charge. UNMETERED (unlimited
    # org) and 0-cost are no-ops. `None` means a metered balance couldn't cover it
    # despite the pre-check (a rare race) — log, don't fail the already-built agent.
    if AGENT_BUILD_CREDIT_SECONDS > 0:
        outcome = await db_client.charge_purchase_tx(
            org,
            AGENT_BUILD_CREDIT_SECONDS,
            kind="agent_build",
            description="Agent builder generation",
            idempotency_key=f"agentbuild:{result['workflow_id']}",
        )
        if outcome is None:
            logger.warning(
                f"Agent-build charge could not be applied for org {org} "
                f"(workflow {result['workflow_id']}) — insufficient balance race"
            )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": result["workflow_id"],
            "workflow_name": result["name"],
            "source": "agent_builder_generate",
            "organization_id": user.selected_organization_id,
        },
    )

    return AgentBuildResponse(**result)
