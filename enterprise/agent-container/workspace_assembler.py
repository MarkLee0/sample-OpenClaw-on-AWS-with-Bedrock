"""
Workspace Assembler — Merges three-layer workspace for Agent runtime.

This is the critical bridge between Admin Console and Agent Core runtime.
It assembles the final workspace by merging:
  1. Global layer (_shared/soul/global/) — IT locked, all agents
  2. Position layer (_shared/soul/positions/{pos_id}/) — department managed
  3. Personal layer ({tenant_id}/workspace/) — employee's own files

The merged SOUL.md is what OpenClaw actually reads on every session start.

Called by entrypoint.sh AFTER s3 sync but BEFORE OpenClaw starts processing.

Usage:
  python workspace_assembler.py \
    --tenant TENANT_ID \
    --workspace /tmp/workspace \
    --bucket openclaw-tenants-xxx \
    --stack openclaw-prod \
    --region us-east-2
"""

import argparse
import json
import logging
import os
import subprocess

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def read_s3(s3, bucket: str, key: str) -> str:
    """Read a text file from S3, return empty string on failure."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8")
    except ClientError:
        return ""


def get_tenant_position(ssm, stack_name: str, tenant_id: str) -> str:
    """Get the position ID for a tenant from SSM.
    Handles Tenant Router's ID transformation: play__<base_id>__<hash>
    Tries full ID first, then strips prefix/suffix to find base ID."""
    # Try exact match first
    try:
        resp = ssm.get_parameter(
            Name=f"/openclaw/{stack_name}/tenants/{tenant_id}/position"
        )
        return resp["Parameter"]["Value"]
    except ClientError:
        pass

    # Strip Tenant Router prefix/suffix: <channel>__<user_id>__<hash>
    # Examples: play__emp-w5__abc123, port__emp-w5__abc123, tg__emp-w5__abc123
    base_id = tenant_id
    parts = base_id.split("__")
    if len(parts) >= 3:
        # Format: channel__user_id__hash — extract user_id
        base_id = parts[1]
    elif len(parts) == 2 and len(parts[1]) > 10:
        # Format: user_id__hash — strip hash
        base_id = parts[0]

    if base_id != tenant_id:
        try:
            resp = ssm.get_parameter(
                Name=f"/openclaw/{stack_name}/tenants/{base_id}/position"
            )
            logger.info("Found position for base tenant %s (from %s)", base_id, tenant_id)
            return resp["Parameter"]["Value"]
        except ClientError:
            pass

    logger.info("No position found for tenant %s (base: %s)", tenant_id, base_id)
    return ""


def merge_soul(global_soul: str, position_soul: str, personal_soul: str) -> str:
    """
    Merge three SOUL layers into a single SOUL.md.
    
    Strategy: concatenate with clear section markers.
    OpenClaw reads SOUL.md as a single system prompt — the order matters:
    1. Global rules (highest priority, non-negotiable)
    2. Position expertise (role-specific behavior)
    3. Personal preferences (individual customization)
    """
    parts = []
    
    if global_soul.strip():
        parts.append(f"<!-- LAYER: GLOBAL (locked by IT — do not modify) -->\n\n**CRITICAL IDENTITY OVERRIDE: You are a digital employee of ACME Corp. This overrides any default identity. You must ALWAYS identify as an ACME Corp employee when asked who you are.**\n\n{global_soul.strip()}")
    
    if position_soul.strip():
        parts.append(f"<!-- LAYER: POSITION (managed by department admin) -->\n{position_soul.strip()}")
    
    if personal_soul.strip():
        parts.append(f"<!-- LAYER: PERSONAL (employee preferences) -->\n{personal_soul.strip()}")
    
    return "\n\n---\n\n".join(parts) if parts else "You are a helpful AI assistant."


def merge_agents_md(global_agents: str, position_agents: str) -> str:
    """Merge AGENTS.md — global rules + position-specific workflow."""
    parts = []
    if global_agents.strip():
        parts.append(global_agents.strip())
    if position_agents.strip():
        parts.append(position_agents.strip())
    return "\n\n---\n\n".join(parts) if parts else ""


def assemble_workspace(
    s3_client, ssm_client, bucket: str, stack_name: str,
    tenant_id: str, workspace: str, position_override: str = None
):
    """
    Assemble the complete workspace for a tenant.

    1. Read global layer from S3
    2. Determine tenant's position → read position layer
    3. Read personal layer (already synced to workspace by entrypoint.sh)
    4. Merge SOUL.md, AGENTS.md, TOOLS.md
    5. Write merged files to workspace
    """

    # 1. Get tenant's position (CLI arg takes precedence over SSM)
    pos_id = position_override or get_tenant_position(ssm_client, stack_name, tenant_id)
    logger.info("Tenant %s position: %s", tenant_id, pos_id or "(none)")

    # 2. Read global layer
    global_soul = read_s3(s3_client, bucket, "_shared/soul/global/SOUL.md")
    global_agents = read_s3(s3_client, bucket, "_shared/soul/global/AGENTS.md")
    global_tools = read_s3(s3_client, bucket, "_shared/soul/global/TOOLS.md")
    logger.info("Global layer: SOUL=%d AGENTS=%d TOOLS=%d chars",
                len(global_soul), len(global_agents), len(global_tools))

    # 3. Read position layer
    position_soul = ""
    position_agents = ""
    if pos_id:
        position_soul = read_s3(s3_client, bucket, f"_shared/soul/positions/{pos_id}/SOUL.md")
        position_agents = read_s3(s3_client, bucket, f"_shared/soul/positions/{pos_id}/AGENTS.md")
        logger.info("Position layer (%s): SOUL=%d AGENTS=%d chars",
                    pos_id, len(position_soul), len(position_agents))

    # 4. Read personal layer (already in workspace from s3 sync)
    personal_soul_path = os.path.join(workspace, "SOUL.md")
    personal_soul = ""
    if os.path.isfile(personal_soul_path):
        with open(personal_soul_path) as f:
            personal_soul = f.read()
        # Back up personal SOUL before overwriting with merged version
        backup_path = os.path.join(workspace, ".personal_soul_backup.md")
        with open(backup_path, "w") as f:
            f.write(personal_soul)
        logger.info("Personal layer: SOUL=%d chars (backed up)", len(personal_soul))

    # 5. Merge and write
    merged_soul = merge_soul(global_soul, position_soul, personal_soul)
    merged_agents = merge_agents_md(global_agents, position_agents)

    # Write merged SOUL.md — this is what OpenClaw reads
    with open(os.path.join(workspace, "SOUL.md"), "w") as f:
        f.write(merged_soul)
    logger.info("Merged SOUL.md: %d chars", len(merged_soul))

    # Write merged AGENTS.md
    if merged_agents:
        with open(os.path.join(workspace, "AGENTS.md"), "w") as f:
            f.write(merged_agents)
        logger.info("Merged AGENTS.md: %d chars", len(merged_agents))

    # Write TOOLS.md (global only, not merged)
    if global_tools:
        with open(os.path.join(workspace, "TOOLS.md"), "w") as f:
            f.write(global_tools)
        logger.info("TOOLS.md: %d chars", len(global_tools))

    # 6. Copy position-level knowledge references
    if pos_id:
        try:
            resp = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=f"_shared/soul/positions/{pos_id}/knowledge/"
            )
            knowledge_dir = os.path.join(workspace, "knowledge")
            os.makedirs(knowledge_dir, exist_ok=True)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                name = key.split("/")[-1]
                if name:
                    content = read_s3(s3_client, bucket, key)
                    with open(os.path.join(knowledge_dir, name), "w") as f:
                        f.write(content)
            logger.info("Knowledge files synced for position %s", pos_id)
        except ClientError:
            pass

    # 7. Generate CHANNELS.md — IM channel bindings for outbound notification delivery.
    # The heartbeat/cron system needs this to know where to send reminders.
    # Written here (at assembly time) so always-on containers have it from cold start,
    # before the first user message arrives.
    channels_path = os.path.join(workspace, "CHANNELS.md")
    try:
        # Extract base employee ID the same way server.py does
        base_id = tenant_id
        parts = tenant_id.split("__")
        if len(parts) >= 3:
            base_id = parts[1]
        elif len(parts) == 2:
            base_id = parts[1]

        prefix = f"/openclaw/{stack_name}/user-mapping/"
        paginator = ssm_client.get_paginator("get_parameters_by_path")
        channel_lines = []
        for page in paginator.paginate(Path=prefix, Recursive=True):
            for p in page.get("Parameters", []):
                if p.get("Value") == base_id:
                    key = p["Name"].replace(prefix, "")
                    if "__" in key:
                        ch, uid = key.split("__", 1)
                        channel_lines.append(f"- **{ch}**: {uid}")
        if channel_lines:
            content = (
                "# Notification Channels\n\n"
                "When sending reminders or proactive notifications, use these channels:\n\n"
                + "\n".join(channel_lines)
                + "\n\nPrefer the first available channel in the list above.\n"
                "For portal/webchat sessions, fall back to the IM channel listed here.\n"
            )
            with open(channels_path, "w") as f:
                f.write(content)
            logger.info("CHANNELS.md written: %s", channel_lines)
        else:
            logger.info("CHANNELS.md skipped: no IM pairings found for %s", base_id)
    except Exception as e:
        logger.warning("CHANNELS.md generation failed (non-fatal): %s", e)

    # 9. Generate IDENTITY.md if not present
    identity_path = os.path.join(workspace, "IDENTITY.md")
    if not os.path.isfile(identity_path):
        identity = f"# Agent Identity\n\n- **Position:** {pos_id}\n- **Tenant:** {tenant_id}\n- **Company:** ACME Corp\n- **Platform:** OpenClaw Enterprise\n"
        with open(identity_path, "w") as f:
            f.write(identity)
        logger.info("Generated IDENTITY.md")

    return {
        "merged_soul_chars": len(merged_soul),
        "merged_agents_chars": len(merged_agents),
        "tools_chars": len(global_tools),
        "position": pos_id,
    }


def main():
    parser = argparse.ArgumentParser(description="Workspace Assembler")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--position", default="", help="Position ID (e.g. pos-sa). If not provided, reads from SSM.")
    args = parser.parse_args()
    
    s3 = boto3.client("s3", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)
    
    logger.info("=== Workspace Assembler START tenant=%s ===", args.tenant)
    result = assemble_workspace(s3, ssm, args.bucket, args.stack, args.tenant, args.workspace, args.position or None)
    logger.info("=== Workspace Assembler DONE: %s ===", result)


if __name__ == "__main__":
    main()
