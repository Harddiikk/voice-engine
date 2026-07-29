"""VoiceLink telephony routes (call-event webhooks).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
import re

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


@router.post("/voicelink/events/{workflow_run_id}")
async def handle_voicelink_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle VoiceLink call-event webhooks.

    VoiceLink POSTs nested camelCase JSON for every call lifecycle event
    (call.initiated, call.ringing, call.answered, call.completed,
    call.ended, call.failed) to the ``webhook_url`` passed in ``add_lead``.
    VoiceLink expects a 200 for valid events — webhooks are unsigned, so
    no signature verification is possible.
    """
    set_current_run_id(workflow_run_id)

    try:
        event_data = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink event body is not valid JSON: {e}"
        )
        return {"status": "error", "reason": "invalid_json"}

    event_type = event_data.get("event", "")
    logger.info(
        f"[run {workflow_run_id}] Received VoiceLink event: event={event_type}"
    )
    logger.debug(
        f"[run {workflow_run_id}] VoiceLink event body: {json.dumps(event_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for VoiceLink event"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    # Parse the nested event into the generic format
    parsed_data = provider.parse_status_callback(event_data)

    logger.debug(
        f"[run {workflow_run_id}] Parsed VoiceLink event: "
        f"call_id={parsed_data['call_id']}, status={parsed_data['status']}"
    )

    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    await _process_status_update(workflow_run_id, status_update)

    logger.info(
        f"[run {workflow_run_id}] VoiceLink event {event_type} processed successfully"
    )

    return {"status": "success"}


def extract_did(raw: str) -> str:
    """Reduce a called-party value from a VoiceLink ``start`` frame to a number.

    VoiceLink is SIP-backed, so the called party can arrive as a bare number
    (``919484959244``), a SIP URI (``sip:919484959244@voicelink.co.in``), a
    user@host pair (``919484959244@10.0.0.1``), or with a ``tel:`` scheme.
    ``normalize_telephony_address`` passes the URI forms through untouched, so
    without this the DID lookup misses and the call is hung up with
    "DID not configured". Strip the scheme and the host before normalizing.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    for scheme in ("sip:", "sips:", "tel:"):
        if value.lower().startswith(scheme):
            value = value[len(scheme) :]
            break
    # Drop any SIP host part, plus a ``;user=phone``-style parameter tail.
    value = value.split("@", 1)[0].split(";", 1)[0]
    return value.strip()


def _did_digits(value: str) -> str:
    """The comparable digit tail of a number — last 10 digits (Indian NSN).

    Runs ``extract_did`` first: digitizing a SIP URI whole would fold the host
    into the number (``919484959244@10.0.0.1`` → ``...5924410001``) and match
    the wrong DID, or nothing at all.

    Used only as a fallback when the canonical forms don't match exactly, so a
    trunk-prefixed or country-code-doubled DID (``0919484959244``) still routes.
    """
    digits = re.sub(r"\D", "", extract_did(value))
    return digits[-10:] if len(digits) >= 10 else digits


async def _read_start_frame(websocket: WebSocket, max_frames: int = 5) -> dict:
    """Read frames until the ``start`` event arrives.

    VoiceLink normally sends ``connected`` then ``start``, but tolerate other
    pre-roll frames (keepalives, a repeated ``connected``) instead of hanging
    up on the first thing that isn't ``start`` — a hangup here looks exactly
    like "inbound doesn't connect" and leaves nothing useful in the log.
    """
    for _ in range(max_frames):
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"VoiceLink INBOUND: non-JSON frame ignored: {raw[:200]}")
            continue
        if not isinstance(msg, dict):
            continue
        event = msg.get("event")
        if event == "start":
            return msg
        logger.info(f"VoiceLink INBOUND: skipping pre-start frame event={event!r}")
    return {}


# Both spellings are registered: a bot URL saved with a trailing slash would
# otherwise fail the WebSocket handshake at the router with nothing logged by
# the app at all, which is indistinguishable from "VoiceLink never called us".
@router.websocket("/ws")
@router.websocket("/ws/")
async def voicelink_inbound_ws(websocket: WebSocket) -> None:
    """VoiceLink WS-only INBOUND entrypoint.

    VoiceLink uses ONE media WebSocket for both directions. Outbound calls
    connect to ``/ws/{workflow_id}/{user_id}/{workflow_run_id}`` (the run is
    pre-created by add_lead). INBOUND calls connect here to the bare bot URL
    (``/api/v1/telephony/ws``) with NO run id, so we read the ``start`` event,
    route by the called DID, create an inbound run, and run the pipeline.

    NOTE: the exact location of the called/caller number in VoiceLink's inbound
    ``start`` event is unconfirmed upstream — the full start frame is logged so
    a real inbound call reveals it; the extraction below tries the common
    locations. Adjust `pick(...)` keys once a real frame is captured.
    """
    # Lazy imports: this module is imported BY api.routes.telephony, so importing
    # its helpers at module load would be circular.
    from sqlalchemy.future import select

    from api.db.models import (
        TelephonyConfigurationModel,
        TelephonyPhoneNumberModel,
    )
    from api.enums import WorkflowRunState
    from api.routes.telephony import _create_inbound_workflow_run
    from api.services.pipecat.run_pipeline import run_pipeline_telephony
    from api.services.telephony.base import NormalizedInboundData
    from api.services.telephony.factory import get_telephony_provider_by_id  # noqa: F401
    from api.utils.telephony_address import normalize_telephony_address

    await websocket.accept()
    try:
        start_msg = await _read_start_frame(websocket)
        if not start_msg:
            logger.error(
                "VoiceLink INBOUND: no 'start' event received within the first "
                "frames — closing"
            )
            await websocket.close(code=4400, reason="Expected start event")
            return

        # Capture the real inbound frame so the DID location can be confirmed.
        logger.info(f"VoiceLink INBOUND start frame (raw): {json.dumps(start_msg)}")

        start_data = start_msg.get("start", {}) or {}
        cp = (
            start_data.get("custom_parameters")
            or start_data.get("customParameters")
            or {}
        )

        def pick(*keys):
            for src in (start_data, cp, start_msg):
                if isinstance(src, dict):
                    for k in keys:
                        v = src.get(k)
                        if v:
                            return v
            return ""

        to_raw = pick("to", "to_number", "called", "called_number", "did", "to_did")
        from_raw = pick("from", "from_number", "caller", "caller_number")
        stream_sid = pick("stream_sid", "streamSid")
        call_sid = pick("call_sid", "callSid")
        logger.info(
            f"VoiceLink INBOUND parsed: to={to_raw!r} from={from_raw!r} "
            f"stream_sid={stream_sid!r} call_sid={call_sid!r}"
        )

        if not to_raw:
            logger.error(
                "VoiceLink INBOUND: no called DID found in start frame — cannot "
                f"route. Frame: {json.dumps(start_msg)}"
            )
            await websocket.close(code=4400, reason="No DID in start event")
            return

        # Strip sip:/tel: scheme and any @host BEFORE normalizing — the
        # normalizer passes URI forms through unchanged, which would make the
        # DID lookup miss and hang the call up as "DID not configured".
        to_norm = normalize_telephony_address(
            extract_did(to_raw), country_hint="IN"
        ).canonical
        from_norm = (
            normalize_telephony_address(
                extract_did(from_raw), country_hint="IN"
            ).canonical
            if from_raw
            else ""
        )
        logger.info(
            f"VoiceLink INBOUND normalized: to={to_raw!r} -> {to_norm!r}, "
            f"from={from_raw!r} -> {from_norm!r}"
        )

        # Route by the called DID alone. VoiceLink's inbound start frame carries
        # NO reseller/account id, so the account-keyed lookup
        # (find_inbound_route_by_account) can't be used — its empty account_id
        # trips an early `return None` guard. The DID is the authorization
        # boundary; it is globally unique in telephony_phone_numbers. This inline
        # join avoids overlaying the baked db client.
        async with db_client.async_session() as session:
            base_query = select(
                TelephonyConfigurationModel, TelephonyPhoneNumberModel
            ).join(
                TelephonyPhoneNumberModel,
                TelephonyPhoneNumberModel.telephony_configuration_id
                == TelephonyConfigurationModel.id,
            )
            result = await session.execute(
                base_query.where(
                    TelephonyConfigurationModel.provider == "voicelink",
                    TelephonyPhoneNumberModel.address_normalized == to_norm,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
            )
            row = result.first()

            if not row:
                # Fallback: match on the last 10 digits. Covers DID spellings
                # the canonical normalizer can't fix on its own — notably a
                # trunk-prefixed "0" in front of the country code
                # ("0919484959244" normalizes to "+91919484959244").
                tail = _did_digits(to_raw)
                if tail:
                    candidates = (
                        (
                            await session.execute(
                                base_query.where(
                                    TelephonyConfigurationModel.provider
                                    == "voicelink",
                                    TelephonyPhoneNumberModel.is_active.is_(True),
                                )
                            )
                        )
                        .all()
                    )
                    for cand in candidates:
                        if _did_digits(cand[1].address_normalized) == tail:
                            logger.warning(
                                f"VoiceLink INBOUND: DID {to_raw!r} matched "
                                f"{cand[1].address_normalized} by digit-tail "
                                f"fallback (canonical form was {to_norm!r})"
                            )
                            row = cand
                            break

        match = (row[0], row[1]) if row else None
        if not match:
            logger.error(
                f"VoiceLink INBOUND: no inbound route for DID {to_norm} "
                f"(raw={to_raw!r}) — check the number is added and active on a "
                f"VoiceLink telephony config"
            )
            await websocket.close(code=4404, reason="DID not configured")
            return

        config, phone_row = match
        if not phone_row.inbound_workflow_id:
            logger.error(
                f"VoiceLink INBOUND: DID {to_norm} has no inbound_workflow_id"
            )
            await websocket.close(code=4404, reason="No workflow for DID")
            return

        workflow_id = phone_row.inbound_workflow_id
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=config.organization_id
        )
        if not workflow:
            logger.error(f"VoiceLink INBOUND: workflow {workflow_id} not found")
            await websocket.close(code=4404, reason="Workflow not found")
            return
        user_id = workflow.user_id

        normalized = NormalizedInboundData(
            provider="voicelink",
            call_id=call_sid or stream_sid or "",
            from_number=from_norm,
            to_number=to_norm,
            direction="inbound",
            call_status="ringing",
            account_id=None,
            from_country="IN",
            to_country="IN",
            raw_data=start_msg,
        )
        run_id = await _create_inbound_workflow_run(
            workflow_id,
            user_id,
            "voicelink",
            normalized,
            telephony_configuration_id=config.id,
            from_phone_number_id=phone_row.id,
        )

        set_current_run_id(run_id)
        await db_client.update_workflow_run(
            run_id=run_id, state=WorkflowRunState.RUNNING.value
        )
        logger.info(
            f"[run {run_id}] VoiceLink INBOUND routed DID {to_norm} -> workflow "
            f"{workflow_id}; starting pipeline"
        )
        await run_pipeline_telephony(
            websocket,
            provider_name="voicelink",
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            user_id=user_id,
            call_id=call_sid or stream_sid or "",
            transport_kwargs={"stream_id": stream_sid, "call_id": call_sid},
        )
        logger.info(f"[run {run_id}] VoiceLink INBOUND pipeline completed")

    except WebSocketDisconnect as e:
        logger.info(
            f"VoiceLink INBOUND ws closed: code={e.code} reason={e.reason!r}"
        )
    except Exception as e:
        logger.error(f"VoiceLink INBOUND ws error: {e}")
        try:
            await websocket.close(1011, "Internal server error")
        except RuntimeError:
            pass
