"""
Audit router — audit/entries, audit/insights, audit/run-scan,
agents/{agent_id}/quality, portal/request-always-on, portal/feedback.

Extracted from main.py.
"""

import os
import re
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

import boto3 as _boto3_audit
from fastapi import APIRouter, HTTPException, Header

import db
from shared import require_auth, require_role, get_dept_scope, GATEWAY_REGION, STACK_NAME

router = APIRouter(tags=["audit"])


# =========================================================================
# Audit — persisted in DynamoDB
# =========================================================================

@router.get("/api/v1/audit/entries")
def get_audit_entries(limit: int = 50, eventType: Optional[str] = None, authorization: str = Header(default="")):
    user = require_auth(authorization)
    limit = min(limit, 200)  # cap to prevent full-table dump
    entries = db.get_audit_entries(limit=limit)
    if eventType:
        entries = [e for e in entries if e.get("eventType") == eventType]
    # Scope for managers — only show events from their department's actors
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            names_in_scope = {e["name"] for e in employees if e.get("departmentId") in scope}
            names_in_scope.add("system")
            names_in_scope.add("Auto-Provision")
            names_in_scope.add("IT Admin")
            entries = [e for e in entries if e.get("actorName") in names_in_scope]
    return entries


def _run_audit_scan() -> dict:
    """Generate real insights from live DynamoDB + S3 data.
    Pattern-based (no LLM). LLM memory analysis is a separate endpoint."""

    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    entries = db.get_audit_entries(limit=200)
    agents = db.get_agents()
    employees = db.get_employees()
    sessions = db.get_sessions()
    insights = []
    idx = 0

    # 1. Repeated permission denials — real data from audit log
    blocked = [e for e in entries if e.get("status") == "blocked"]
    if blocked:
        # Group by actor
        by_actor: dict = {}
        for e in blocked:
            actor = e.get("actorName", "unknown")
            by_actor[actor] = by_actor.get(actor, [])
            by_actor[actor].append(e)
        # Flag actors with 3+ blocked attempts
        repeat_blockers = {k: v for k, v in by_actor.items() if len(v) >= 2}
        if repeat_blockers:
            actor_names = list(repeat_blockers.keys())[:3]
            total = sum(len(v) for v in repeat_blockers.values())
            top_tool = ""
            for e in blocked:
                m = re.search(r'(shell|browser|code_execution|file_write)', e.get("detail", ""), re.I)
                if m:
                    top_tool = m.group(1)
                    break
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "high", "category": "access_pattern",
                "title": f"{total} permission denials — {len(repeat_blockers)} repeat offenders",
                "description": f"Detected {total} blocked operations across {len(repeat_blockers)} employees in the last 50 audit events. Top blocked tool: {top_tool or 'various'}. Repeated denials may indicate misconfigured SOUL permissions or employee confusion about allowed tools.",
                "recommendation": "Review SOUL tool permissions for affected positions. Consider adding a permission escalation request workflow so employees can request access instead of being silently blocked.",
                "affectedUsers": actor_names,
                "detectedAt": now_str,
                "source": "audit_log_scan",
            })
            idx += 1

    # 2. SOUL version drift — agents where soulVersions.position < latest
    pos_versions: dict = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    drifted = []
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos in pos_versions and sv < pos_versions[pos]:
            emp = next((e for e in employees if e.get("id") == a.get("employeeId")), {})
            drifted.append(emp.get("name", a.get("employeeName", a["id"])))
    if drifted:
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "high", "category": "compliance",
            "title": f"SOUL version drift — {len(drifted)} agent(s) behind",
            "description": f"{len(drifted)} agent(s) are running outdated position SOUL templates. Policy changes made to position SOULs have not been propagated to these agents, meaning security and behavior rules may not be current.",
            "recommendation": "Trigger workspace reassembly for affected agents via Agent Factory → SOUL Editor. Consider auto-incrementing position SOUL version on each workspace assembly.",
            "affectedUsers": drifted[:5],
            "detectedAt": now_str,
            "source": "version_drift_check",
        })
        idx += 1

    # 3. Zero-turn agents — agents with no sessions at all
    agents_with_sessions = {s.get("agentId") for s in sessions}
    unengaged = [a for a in agents if a.get("id") not in agents_with_sessions and a.get("employeeId")]
    if unengaged:
        names = [next((e.get("name", "") for e in employees if e.get("id") == a.get("employeeId")), a.get("employeeName", "")) for a in unengaged[:3]]
        names = [n for n in names if n]
        if names:
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "low", "category": "optimization",
                "title": f"{len(unengaged)} employee agent(s) with no recorded sessions",
                "description": f"{len(unengaged)} personal agents have never recorded a session. These employees may not be aware of their agent, or onboarding hasn't been completed. Low engagement reduces ROI.",
                "recommendation": "Send an onboarding nudge to affected employees. Verify IM channel bindings are configured. Check if pairing was completed in Bindings → IM User Mappings.",
                "affectedUsers": names,
                "detectedAt": now_str,
                "source": "engagement_analysis",
            })
            idx += 1

    # 4. Config changes spike — if many config changes in audit log
    config_changes = [e for e in entries if e.get("eventType") == "config_change"]
    if len(config_changes) >= 5:
        changers = list({e.get("actorName", "") for e in config_changes})[:3]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "compliance",
            "title": f"{len(config_changes)} configuration changes detected",
            "description": f"{len(config_changes)} config change events recorded in recent audit log. High change velocity can introduce policy inconsistencies or unintended agent behavior changes.",
            "recommendation": "Review recent config changes in Audit Center → Event Timeline filtered by 'Config Change'. Enable change approval workflow for SOUL and permission edits.",
            "affectedUsers": changers,
            "detectedAt": now_str,
            "source": "audit_log_scan",
        })
        idx += 1

    # 5. Agents missing bindings — created but no channel binding
    bound_agent_ids = {b.get("agentId") for b in (db.get_bindings() if hasattr(db, "get_bindings") else [])}
    unbound_agents = [a for a in agents if a.get("employeeId") and a.get("id") not in bound_agent_ids]
    if unbound_agents:
        names_ub = [a.get("employeeName", a.get("id", "")) for a in unbound_agents[:3]]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "optimization",
            "title": f"{len(unbound_agents)} agent(s) without IM channel binding",
            "description": f"{len(unbound_agents)} personal agents exist in the system but have no IM channel binding. These agents cannot receive messages from employees via Discord, Slack, or other channels.",
            "recommendation": "Go to Bindings & Routing → Create Binding to link these agents to the appropriate IM channel. Or use Bulk Assign by Position.",
            "affectedUsers": names_ub,
            "detectedAt": now_str,
            "source": "binding_scan",
        })
        idx += 1

    return {
        "insights": insights,
        "summary": {
            "totalInsights": len(insights),
            "high": len([i for i in insights if i["severity"] == "high"]),
            "medium": len([i for i in insights if i["severity"] == "medium"]),
            "low": len([i for i in insights if i["severity"] == "low"]),
            "lastScanAt": now_str,
            "scanSources": ["audit_log", "agent_soul_versions", "session_data", "binding_registry"],
        }
    }


# Cache last scan result in memory (reset on server restart)
_audit_scan_cache: dict = {}


@router.get("/api/v1/audit/insights")
def get_audit_insights():
    """Return cached scan results (or empty if never scanned)."""
    global _audit_scan_cache
    if not _audit_scan_cache:
        # Run once on first load
        _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


@router.post("/api/v1/audit/run-scan")
def run_audit_scan():
    """Trigger a fresh audit scan. Returns updated insights."""
    global _audit_scan_cache
    _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


def _calculate_agent_quality(agent_id: str) -> dict:
    """Calculate real quality score for an agent from DynamoDB data.

    Quality Score = 0.3*satisfaction + 0.2*tool_success + 0.2*response_time + 0.2*compliance + 0.1*completion

    Data sources:
    - Satisfaction: FEEDBACK# records (thumbs up rate)
    - Tool success: AUDIT# records (agent_invocation success rate)
    - Response time: SESSION# durationMs (P75 < 8s = full score)
    - Compliance: AUDIT# permission_denied rate (low = good)
    - Completion: SESSION# turns > 1 rate
    """
    try:
        ddb = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)

        # Tool success + compliance from AUDIT# entries (last 7 days)
        audit_resp = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": "ORG#acme", ":sk": "AUDIT#"},
            ScanIndexForward=False, Limit=200)
        agent_audits = [a for a in audit_resp.get("Items", [])
                        if a.get("targetId") == agent_id]
        invocations = [a for a in agent_audits if a.get("eventType") == "agent_invocation"]
        permission_denials = [a for a in agent_audits if a.get("eventType") == "permission_denied"]
        tool_success = 1.0 if not invocations else sum(
            1 for a in invocations if a.get("status") == "success") / max(1, len(invocations))
        compliance = 1.0 if not invocations else max(0, 1 - len(permission_denials) / max(1, len(invocations)))

        # Response time from SESSION# records
        sessions = [s for s in db.get_sessions() if s.get("agentId") == agent_id]
        durations = [float(s["durationMs"]) for s in sessions if s.get("durationMs")]
        if durations:
            p75 = sorted(durations)[int(len(durations) * 0.75)]
            response_score = min(1.0, max(0, 1.0 - (p75 - 3000) / 12000))  # 3s=1.0, 15s=0.0
        else:
            response_score = 0.7  # neutral default

        # Completion rate (sessions with >1 turn)
        multi_turn = [s for s in sessions if int(s.get("turns", 0)) > 1]
        completion = len(multi_turn) / max(1, len(sessions)) if sessions else 0.7

        # Satisfaction from FEEDBACK# (explicit thumbs up/down)
        feedback_resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={":pk": "TYPE#feedback", ":sk": f"FEEDBACK#{agent_id}"},
            Limit=100)
        feedbacks = feedback_resp.get("Items", [])
        if feedbacks:
            positive = sum(1 for f in feedbacks if f.get("rating") == "up")
            satisfaction = positive / len(feedbacks)
        else:
            # Fall back to audit success rate as proxy for satisfaction
            satisfaction = tool_success * 0.9 + 0.1

        score = round(
            0.3 * satisfaction + 0.2 * tool_success +
            0.2 * response_score + 0.2 * compliance + 0.1 * completion, 2)

        return {
            "score": round(score * 5, 1),  # 0-5 scale
            "breakdown": {
                "satisfaction": round(satisfaction * 5, 1),
                "toolSuccess": round(tool_success * 5, 1),
                "responseTime": round(response_score * 5, 1),
                "compliance": round(compliance * 5, 1),
                "completion": round(completion * 5, 1),
            },
            "dataPoints": {
                "invocations": len(invocations),
                "sessions": len(sessions),
                "feedbacks": len(feedbacks),
            }
        }
    except Exception as e:
        return {"score": None, "error": str(e)}


@router.get("/api/v1/agents/{agent_id}/quality")
def get_agent_quality(agent_id: str, authorization: str = Header(default="")):
    """Get real quality score for an agent calculated from DynamoDB data."""
    require_auth(authorization)
    return _calculate_agent_quality(agent_id)


@router.post("/api/v1/portal/request-always-on")
def request_always_on(body: dict, authorization: str = Header(default="")):
    """Employee requests always-on mode for their agent.
    Creates a pending approval that IT admin can approve/deny."""
    user = require_auth(authorization)
    reason = body.get("reason", "").strip() or "Employee-initiated request"

    # Check not already always-on
    stack = os.environ.get("STACK_NAME", "openclaw")
    try:
        ssm_chk = _boto3_audit.client("ssm", region_name=GATEWAY_REGION)
        ssm_chk.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        raise HTTPException(400, "Already in always-on mode")
    except HTTPException:
        raise
    except Exception:
        pass

    approval_id = f"apr-alwayson-{user.employee_id}"
    db.create_approval({
        "id": approval_id,
        "type": "always_on_request",
        "requestedBy": user.employee_id,
        "requestedByName": user.name,
        "reason": reason,
        "status": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "details": {
            "employeeId": user.employee_id,
            "currentMode": "serverless",
            "requestedMode": "always-on-ecs",
        }
    })
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "always_on_request", "actorId": user.employee_id,
        "actorName": user.name, "targetType": "agent", "targetId": user.employee_id,
        "detail": f"Employee requested always-on mode: {reason}", "status": "pending",
    })
    return {"requested": True, "approvalId": approval_id,
            "message": "Request submitted. IT admin will review and activate always-on mode for your agent."}


@router.post("/api/v1/portal/feedback")
def submit_feedback(body: dict, authorization: str = Header(default="")):
    """Employee submits thumbs up/down feedback on an agent response."""
    user = require_auth(authorization)
    session_id = body.get("sessionId", "")
    turn_seq = body.get("turnSeq", 0)
    rating = body.get("rating", "")  # "up" or "down"
    agent_id = body.get("agentId", "")

    if rating not in ("up", "down"):
        raise HTTPException(400, "rating must be 'up' or 'down'")

    try:
        ddb = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        fid = f"{session_id}#{turn_seq:04d}"
        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"FEEDBACK#{fid}",
            "GSI1PK": "TYPE#feedback",
            "GSI1SK": f"FEEDBACK#{agent_id}#{fid}",
            "sessionId": session_id,
            "turnSeq": Decimal(str(turn_seq)),
            "rating": rating,
            "employeeId": user.employee_id,
            "agentId": agent_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")

    return {"saved": True, "rating": rating}
