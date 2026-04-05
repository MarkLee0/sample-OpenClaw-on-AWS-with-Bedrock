# Always-on Agent (ECS Fargate) — Interaction Design

## Status: DRAFT

## Why Always-on Exists

Serverless (AgentCore microVM) scales to zero — great for cost, bad for:

| Use case | Why serverless fails |
|----------|---------------------|
| Check email every 3 minutes | microVM is gone between invocations — no cron process |
| Customer service center 7x24 | 6-10s cold start per message — unacceptable for live chat |
| Proactive reminders (HEARTBEAT) | Only fires "next time someone messages" — may be hours late |
| Direct IM bot connection | Container needs persistent WebSocket/long-poll to Telegram/Discord |
| Background data sync | Needs continuous process: watch inbox, scan tickets, update KB |

**Core value: the OpenClaw Gateway process inside the container stays alive.** It maintains IM connections, runs scheduled tasks, and responds instantly.

---

## One Model, Not Two

There is no "shared agent" vs "personal agent" infrastructure distinction. Every agent is the same:

- One ECS Fargate container per agent
- Same Docker image, same code path
- Admin assigns 1 or N employees — that's the only difference
- Every agent naturally serves multiple callers (employee + Digital Twin visitors)

```
agent-exec-dickson (1 employee assigned):
  ├── Dickson himself (Portal / IM)
  ├── Dickson's Digital Twin visitors
  └── HEARTBEAT: check email q3m, daily report

agent-helpdesk (12 employees assigned):
  ├── Carol, Ryan, Mike, ... (all assigned employees)
  ├── Per-employee workspace isolation (EFS /mnt/efs/{emp_id}/)
  └── Optional: dedicated @acme_helpdesk Telegram bot

Same container architecture. Same code. Different assignment count.
```

**Proof:** Dickson's agent (`agent-exec-dickson`) was launched with `start_always_on_agent()` — the same function used for helpdesk. The `_launch_personal_always_on()` function is dead code and should be removed.

---

## Admin Lifecycle: From Creation to Daily Operation

### Phase 1: Create Agent (Agent Factory)

```
Admin Console → Agent Factory → Create Agent
  ├── Agent Name: "IT Helpdesk"
  ├── Position: pos-helpdesk (determines SOUL template)
  ├── Deployment Mode: ○ Serverless (default)  ● Always-on (ECS Fargate)
  └── [Create]
```

If "Always-on" is selected, additional config appears:

```
Always-on Configuration:
  ├── Resource Tier: ○ Small (0.25 vCPU / 0.5 GB)  ● Standard (0.5 vCPU / 1 GB)  ○ Large (1 vCPU / 2 GB)
  ├── Auto-restart on failure: ● Yes  ○ No
  └── IM Direct Connection (optional):
      ├── Telegram Bot Token: [__________] (SecureString, stored in SSM)
      └── Discord Bot Token:  [__________]
```

**What happens on Create:**
1. DynamoDB: AGENT# record created with `deployMode: "always-on"`, `containerStatus: "pending"`
2. S3: SOUL template + workspace seeded
3. SSM: tenant-position mapping written
4. **No ECS task started yet** — admin must explicitly "Start" after verifying config

### Phase 2: Configure IM Binding

There are two IM connection models. Admin chooses based on use case:

#### Model 1: Shared Gateway Bot (default)

All employees message the same company bot (@acme_bot). The Gateway EC2 routes messages to the always-on container via Tenant Router.

```
User messages @acme_bot on Telegram
  → Gateway EC2 receives message
  → H2 Proxy extracts sender identity
  → Tenant Router: resolve user → emp_id → check always-on assignment
  → Route to Fargate container endpoint (SSM lookup)
  → Container processes, returns response
  → Gateway sends reply via @acme_bot
```

**Admin actions:**
- Assign employees to this agent (Bindings page or Agent detail)
- Employee self-service pairs via Portal → Connect IM → QR code
- Admin sees all paired employees in IM Channels page

**Pros:** One bot for entire org. Employees self-service pair.
**Cons:** Messages route through Gateway EC2 (extra hop). Not suitable for external-facing bots.

#### Model 2: Dedicated Bot (direct IM)

The always-on container has its own bot token. It connects directly to Telegram/Discord — no Gateway relay.

```
Anyone messages @acme_helpdesk on Telegram
  → Telegram API delivers to Fargate container directly
  → OpenClaw Gateway inside container processes message
  → Container replies directly to Telegram
  → No Gateway EC2 involved
```

**Admin actions:**
- Create dedicated bot on Telegram/Discord (admin does this in the IM platform)
- Enter bot token in Agent Factory → Always-on Configuration → IM Direct Connection
- Token stored as SSM SecureString, injected as env var on container start
- Admin manages who can talk to this bot via OpenClaw's allowFrom config (or open to all)

**Pros:** Direct connection, lower latency. External-facing (customers, partners can use).
**Cons:** Separate bot per agent. No employee self-service pairing (it's a dedicated service bot).

#### When to use which

| Scenario | Model | Why |
|----------|-------|-----|
| Internal helpdesk | 1 (Shared Gateway) | Employees already paired with @acme_bot |
| Customer service center | 2 (Dedicated Bot) | Customers message @acme_support directly, not an internal bot |
| Executive assistant (personal) | 1 or 2 | 1 if exec already uses @acme_bot; 2 if exec wants private bot |
| Email/calendar monitor | Neither | No IM needed — agent runs background tasks silently, notifies via CHANNELS.md |

### Phase 3: Start Agent

```
Admin Console → Agent Factory → agent-helpdesk → [Start Always-on]
```

**What happens:**
1. Admin Console calls `POST /api/v1/admin/always-on/{agent_id}/start`
2. Backend resolves ECS config (cluster, task definition, subnet, SG)
3. If existing task running → `ecs.stop_task()` first (with wait)
4. `ecs.create_service()` or `ecs.run_task()` with:
   - Container overrides: SESSION_ID, SHARED_AGENT_ID, S3_BUCKET, EFS_ENABLED=true, bot tokens
   - Network: subnet + security group
   - EFS volume mount
5. Container starts → entrypoint.sh:
   a. EFS workspace setup (`/mnt/efs/{base_id}/workspace/`)
   b. S3 bootstrap (first start only — EFS empty)
   c. Workspace assembly (SOUL merge, skill loading, KB injection)
   d. Bot token injection into openclaw.json
   e. OpenClaw Gateway starts (port 18789) — connects to Telegram/Discord if tokens present
   f. server.py starts (port 8080) — ready for /invocations
   g. SSM endpoint registration (`http://{task_ip}:8080`)
6. Admin Console shows: Status = Running, Endpoint = registered

### Phase 4: Assign Employees (Shared Agent)

```
Admin Console → Agent Factory → agent-helpdesk → Assignments tab
  ├── [+ Assign Employee]
  │   ├── Search: Carol Zhang
  │   └── [Assign] → SSM: /openclaw/{stack}/tenants/emp-carol/always-on-agent = agent-helpdesk
  ├── Current Assignments:
  │   ├── Carol Zhang (Finance) — assigned 2026-04-01 — [Remove]
  │   ├── Ryan Park (Engineering) — assigned 2026-04-03 — [Remove]
  │   └── Mike Johnson (Sales) — assigned 2026-04-05 — [Remove]
  └── [Assign Entire Position] → Bulk assign all employees with a given position
```

**What happens when assigned:**
- SSM parameter written: `/openclaw/{stack}/tenants/{emp_id}/always-on-agent` = `{agent_id}`
- Tenant Router reads this on next request → routes employee to the container
- Employee continues using their existing IM channel (@acme_bot) — no re-pairing needed
- Employee's workspace is assembled on first message to the always-on container (EFS per-employee isolation)

**What happens when removed:**
- SSM parameter deleted
- Tenant Router falls back to AgentCore serverless on next request
- Employee's memory is preserved in S3 (cross-mode handoff via EFS→S3 snapshot)

### Phase 5: Daily Admin Operations

#### Monitor

```
Admin Console → Monitor → Always-on Agents tab
  ├── agent-helpdesk
  │   ├── Status: ● Running (uptime: 3d 14h)
  │   ├── Fargate Task: arn:aws:ecs:us-east-1:123456:task/xxx
  │   ├── Endpoint: http://10.0.1.50:8080 (healthy ✓)
  │   ├── IM Connections: Telegram @acme_helpdesk (connected ✓)
  │   ├── Assigned Employees: 12
  │   ├── Today: 47 invocations, 3,200 tokens
  │   ├── Active HEARTBEATs: 3 (email check q3m, ticket scan q5m, daily report)
  │   └── Last restart: 2026-04-03 14:22 (admin-initiated)
  └── agent-hr-bot
      ├── Status: ○ Stopped
      └── [Start]
```

#### Config Changes (propagation)

| Admin action | Propagation to always-on container |
|-------------|-----------------------------------|
| Edit Global SOUL | `_config_version` poll (5 min) → re-assembly |
| Edit Position SOUL | `_config_version` poll (5 min) → re-assembly |
| Edit agent model config | `_config_version` poll (5 min) → re-read from DynamoDB |
| Change employee assignment | SSM write → Tenant Router picks up within 60s (cache TTL) |
| Update bot token | Store in SSM → [Reload] container (stop + start with new token) |
| Update Docker image | [Reload] with new image tag → ECS stops old, starts new task |
| Emergency: force refresh | [Restart] → stops task, starts new task, full re-assembly from S3 |

#### Alerts (auto-detect)

| Event | Detection | Admin notification |
|-------|-----------|-------------------|
| Container crashed | ECS task status → STOPPED unexpectedly | Alert in Monitor + notification bell |
| Container OOM | ECS task stop reason = OutOfMemoryError | Alert + recommend larger resource tier |
| IM connection lost | Container logs: "Telegram disconnected" | Alert + suggest [Restart] |
| Endpoint unreachable | Tenant Router health check fails | Alert + auto-restart (if ECS Service) |
| High latency | CloudWatch: p99 response time > 10s | Alert in Monitor |

---

## Routing Logic (Tenant Router)

```
Message arrives at Tenant Router /route
  │
  ├── Resolve user_id → emp_id (DynamoDB MAPPING# / SSM)
  │
  ├── Check: is emp_id assigned to always-on agent?
  │   SSM: /openclaw/{stack}/tenants/{emp_id}/always-on-agent
  │   │
  │   ├── YES → Get endpoint
  │   │   SSM: /openclaw/{stack}/always-on/{agent_id}/endpoint
  │   │   │
  │   │   ├── Endpoint exists → invoke container directly
  │   │   │   POST http://{task_ip}:8080/invocations
  │   │   │   Headers: X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: {tenant_id}
  │   │   │
  │   │   └── Endpoint missing → container not running
  │   │       Return 502 "Agent container not available. Contact admin."
  │   │
  │   └── NO → Normal AgentCore path
  │       3-tier runtime routing: employee override → position rule → default
  │       invoke_agent_runtime() → Firecracker microVM
  │
  └── Not resolved (unknown user) → AgentCore with raw user_id
```

**Key: always-on check happens BEFORE AgentCore routing.** If an employee is assigned to an always-on agent, they ALWAYS go to the container — even if the container happens to be down. This is intentional: the admin chose always-on for reliability, falling back to serverless would change behavior (different SOUL, different memory context).

If the container is down and the admin wants to temporarily route to serverless, they remove the assignment.

---

## IM Binding Matrix

| IM model | Who creates the bot | Who pairs employees | How messages reach the container | Admin control |
|----------|--------------------|--------------------|--------------------------------|---------------|
| **Shared Gateway Bot** | IT admin (one-time, org-wide) | Employee self-service (QR code) | Gateway EC2 → H2 Proxy → Tenant Router → Fargate | Assign/unassign employees. Revoke IM connections. |
| **Dedicated Bot** | IT admin (per agent) | Bot token entered in Agent Factory | Telegram/Discord API → Fargate container directly | Rotate bot token. Control allowFrom list. |
| **No IM** (background only) | N/A | N/A | No inbound messages. Agent runs scheduled tasks. Outbound via CHANNELS.md. | Configure HEARTBEAT schedules. Monitor execution logs. |

### What Admin Can Do After Binding

| Action | Shared Gateway | Dedicated Bot |
|--------|---------------|--------------|
| See all connected employees | IM Channels page (per-channel table) | Agent detail → Connected Users |
| Disconnect an employee | IM Channels → [Disconnect] | Remove from allowFrom (or they keep access) |
| See conversation history | Monitor → Sessions → click session | Same |
| Takeover a session | Monitor → Sessions → [Takeover] | Same |
| Rate-limit an employee | Future: per-employee throttle config | Same |
| Block a user | Remove IM binding (DynamoDB MAPPING#) | Remove from allowFrom / revoke bot token |
| Audit all messages | Audit Center → filter by agent | Same |
| See bot health | IM Channels → connection status | Agent detail → IM Status (connected/disconnected) |

---

## ECS Fargate Resource Architecture

```
CloudFormation Stack
├── ECS Cluster: {stack}-always-on
│   └── Capacity Provider: FARGATE
│
├── ECS Task Definition: {stack}-always-on-agent
│   ├── Container: always-on-agent
│   │   ├── Image: {account}.dkr.ecr.{region}.amazonaws.com/{stack}-agent:latest
│   │   ├── Port: 8080
│   │   ├── CPU: 512 (overridable per agent)
│   │   ├── Memory: 1024 (overridable per agent)
│   │   ├── Mount: /mnt/efs → EFS volume
│   │   └── Env vars: injected at RunTask/CreateService time
│   └── Volume: always-on-workspace → EFS FileSystem
│
├── EFS: {stack}-always-on-workspace
│   ├── Encrypted, generalPurpose, elastic throughput
│   ├── Lifecycle: IA after 7 days, back to primary on access
│   └── Mount Target in task subnet
│
├── Security Groups:
│   ├── EFS SG: allow NFS (2049) from EC2 SG + ECS Task SG
│   └── Task SG: allow 8080 from EC2 SG only
│
├── IAM:
│   ├── Execution Role: pull ECR + write CloudWatch Logs
│   └── Task Role: Bedrock, S3, DynamoDB, SSM, EFS
│
└── (No ECS Service — created dynamically by Admin Console)
    Each always-on agent becomes one ECS Service with desiredCount=0 or 1
```

### Why no static ECS Service in CFn

Different customers have different always-on agents. The CloudFormation template provides the infrastructure (cluster, task def, EFS, IAM). The Admin Console dynamically creates/manages ECS Services per agent.

This is similar to how AWS Copilot or ECS Compose work — base infrastructure in CFn, per-service lifecycle via API.

---

## Open Design Decisions

### OD1: ECS Service vs RunTask

Current: `ecs.run_task()` (manual, no auto-restart)
Proposed: `ecs.create_service()` with `desiredCount=1` per agent

| | RunTask | CreateService |
|-|---------|--------------|
| Auto-restart on crash | No | Yes |
| Rolling update on image change | No | Yes (via force-new-deployment) |
| Health check integration | No | Yes (ELB or container health check) |
| Stop | `stop_task()` | `update_service(desiredCount=0)` |
| Start | `run_task()` | `update_service(desiredCount=1)` |
| Admin Console complexity | Lower | Slightly higher (track service name) |

**Recommendation:** ECS Service. For enterprise delivery, auto-restart is non-negotiable.

### OD2: Dedicated Bot — allowFrom Management

When a dedicated Telegram bot is connected, who can message it?

Options:
- A. **Open to all** — anyone who finds the bot can chat (good for customer service)
- B. **allowFrom list** — only approved users can chat (managed in Admin Console)
- C. **Pairing flow** — users must pair first (like the shared gateway bot)

For customer service center: A (open)
For internal helpdesk with dedicated bot: B (allowFrom)
For executive assistant: B (only the exec)

Admin Console needs a way to configure this per agent.

### OD3: Personal Always-on — Approval Flow

The current code has `_launch_personal_always_on()` but no caller.

Should personal always-on be:
- A. Admin-only toggle (Agent Factory → agent detail → Deploy Mode)
- B. Employee request → admin approval → auto-activate
- C. Position-level default (all executives get always-on automatically)

**Recommendation:** A for v1. Admin toggles per agent in Agent Factory. B and C are enhancements.

---

## Bugs to Fix (from audit)

| # | Bug | Fix | Priority |
|---|-----|-----|----------|
| H2 | Shared agent missing `EFS_ENABLED=true` | Add env var in `start_always_on_agent()` | P0 |
| H5 | DynamoDB UpdateExpression syntax error | Fix SET/REMOVE syntax in `stop_always_on_agent()` | P0 |
| H6 | Workspace pollution (solved by H2+EFS) | EFS gives per-employee dirs automatically | P0 |
| M2 | Cleanup doesn't deregister SSM endpoint | Add `ssm.delete-parameter` in `entrypoint.sh cleanup()` | P0 |
| M4 | Stop failure → double container | Check task status before starting new one | P1 |
| M5 | deployMode set to "personal" on stop | Change to "serverless" or keep "always-on" with status "stopped" | P1 |
| H3 | Personal always-on endpoint never registered | Add `PERSONAL_AGENT_ID` env var or reuse `SHARED_AGENT_ID` | P2 |
| H4 | `_launch_personal_always_on` never called | Wire up from Agent Factory UI (OD3 decision) | P2 |
| H1 | No ECS Service (no auto-restart) | Migrate from RunTask to CreateService (OD1 decision) | P1 |
