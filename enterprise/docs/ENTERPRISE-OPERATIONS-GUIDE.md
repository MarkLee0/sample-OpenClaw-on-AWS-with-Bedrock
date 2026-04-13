# OpenClaw Enterprise — Deployment & Operations Guide

> Complete guide for deploying the platform, configuring it for your organization, and onboarding employees.

---

## 1. Prerequisites

| Requirement | Check Command | Minimum Version |
|-------------|---------------|-----------------|
| AWS CLI | `aws --version` | >= 2.27 (for AgentCore) |
| Python | `python3 --version` | >= 3.10 |
| SSM Plugin | `session-manager-plugin --version` | Latest |
| AWS Credentials | `aws sts get-caller-identity` | Must succeed |
| Bedrock Model Access | AWS Console → Bedrock → Model access | Enable your chosen model |

**Bedrock Model Access:** Before deploying, go to [Amazon Bedrock Console](https://console.aws.amazon.com/bedrock/home#/modelaccess) → "Manage model access" → Enable models for your 4 tiers. Recommended: MiniMax M2.5 (Standard), DeepSeek V3.2 (Restricted), Claude Sonnet 4.5 (Engineering), Claude Sonnet 4.6 (Executive).

**IAM Permissions** for the deploying user: CloudFormation, EC2, S3, ECR, SSM, DynamoDB, ECS, EFS, IAM (create roles), Bedrock, CloudWatch Logs.

---

## 2. Deployment

### 2.1 Configure

```bash
cd enterprise
cp .env.example .env
```

Edit `.env` — only 3 values are required:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `STACK_NAME` | Yes | `openclaw` | Names all resources. Must be unique per account/region. |
| `REGION` | Yes | `us-east-1` | Any region with Bedrock + AgentCore (us-east-1, us-west-2, ap-northeast-1, etc.). |
| `ADMIN_PASSWORD` | Yes | — | Initial password for all accounts. Employees must set a personal password on first login. |
| `MODEL` | No | `minimax.minimax-m2.5` | Default Bedrock model ID (Standard tier). Each tier can use a different model. |
| `INSTANCE_TYPE` | No | `c7g.large` | `t4g.small` for testing, `c7g.large` for production. |
| `DYNAMODB_TABLE` | No | = STACK_NAME | **Must equal STACK_NAME** (IAM policy constraint). Leave empty. |
| `DYNAMODB_REGION` | No | = REGION | Can differ from main REGION if needed. |

All other variables have sensible defaults. See `.env.example` for the full list.

### 2.2 Deploy

```bash
bash deploy.sh
```

The script runs 8 steps (~30 minutes total):

| Step | What It Does | Time |
|------|-------------|------|
| 1/8 Prerequisites | Validates AWS CLI version | 5s |
| 2/8 CloudFormation | Creates VPC, EC2, S3, ECR, IAM, ECS, EFS | 8 min |
| 3/8 Docker Build | Builds agent container on EC2, pushes to ECR | 15 min |
| 4/8 AgentCore Runtime | Creates Bedrock AgentCore Runtime | 30s |
| 5/8 S3 Upload | Uploads SOUL templates + knowledge docs | 10s |
| 6/8 DynamoDB Seed | Creates table, seeds org data (20 employees, 10 positions) | 30s |
| 7/8 Secrets | Stores admin password + JWT secret in SSM | 20s |
| 8/8 EC2 Services | Builds frontend, installs services, starts systemd | 5 min |

**Expected final output:**
```
[ok]  Deployment complete!
      Stack:   openclaw-enterprise
      Runtime: <runtime-id>
      S3:      openclaw-tenants-<account-id>
      EC2:     i-<instance-id>

  Access Admin Console:
    aws ssm start-session --target i-<instance-id> ...
    → Open http://localhost:8099
    → Login: emp-jiade / password: <your ADMIN_PASSWORD>
```

**Re-deploy options:**
```bash
bash deploy.sh --skip-build      # Skip Docker build (use existing image)
bash deploy.sh --skip-seed       # Skip DynamoDB seeding (data already exists)
bash deploy.sh --skip-services   # Skip service deployment (infrastructure only)
```

---

## 3. Post-Deployment Verification

### 3.1 Connect to EC2

```bash
# SSM shell (no SSH key needed)
aws ssm start-session --target <INSTANCE_ID> --region <REGION>
```

### 3.2 Check Services

```bash
# All 4 services should be "active"
systemctl is-active openclaw-admin tenant-router bedrock-proxy-h2
sudo -H -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active openclaw-gateway

# All 4 ports should be listening
ss -tlnp | grep -E '8090|8091|8099|18789'
# Expected: 8090 (tenant-router), 8091 (h2-proxy), 8099 (admin), 18789 (gateway)
```

### 3.3 Check Configuration

```bash
# Environment file should exist with all variables
cat /etc/openclaw/env

# DynamoDB should be accessible (returns JSON with employee count)
curl -s http://localhost:8099/api/v1/org/employees | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(f'Employees: {len(d)}')"
# Expected: Employees: 27
```

### 3.4 Check S3 Templates

```bash
BUCKET=$(grep S3_BUCKET /etc/openclaw/env | cut -d= -f2)
aws s3 ls "s3://$BUCKET/_shared/soul/global/"
# Expected: SOUL.md, AGENTS.md, TOOLS.md
aws s3 ls "s3://$BUCKET/_shared/soul/positions/" | head -5
# Expected: pos-sa/, pos-sde/, pos-pm/, etc.
```

### 3.5 Test Login

```bash
ADMIN_PW=$(grep ADMIN_PASSWORD /etc/openclaw/env | cut -d= -f2)
curl -s -X POST http://localhost:8099/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"employeeId\":\"emp-jiade\",\"password\":\"$ADMIN_PW\"}" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('Login OK' if 'token' in d else f'FAIL: {d}')"
# Expected: Login OK
```

If all checks pass, your platform is ready.

---

## 4. Admin Day-1 Workflow

After deployment, the admin has 6 tasks to make the platform operational.

### 4.1 Access Admin Console

```bash
# From your local machine:
aws ssm start-session --target <INSTANCE_ID> --region <REGION> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8099"],"localPortNumber":["8099"]}'

# Open browser: http://localhost:8099
# Login: emp-jiade / <your ADMIN_PASSWORD>
```

### 4.2 Review Organization Structure

Go to **Organization** (sidebar) to see pre-seeded data:
- **Departments:** 5 departments (Engineering, Sales, Product, Finance, Legal, etc.)
- **Positions:** 10 positions (Solutions Architect, SDE, PM, Finance Analyst, etc.)
- **Employees:** 27 demo employees across all positions

Each employee automatically gets a Serverless agent based on their position's SOUL template.

### 4.3 Configure SOUL (Agent Identity)

SOUL defines *who the agent is*. It uses 3-layer inheritance:

```
Global SOUL (all agents)  →  Position SOUL (per role)  →  Personal SOUL (per employee)
```

**Edit Global SOUL:** Security Center → Policies tab → "Edit Global SOUL"
- This is the base layer for ALL agents. Define company-wide rules here.
- Example: "You are a digital employee of ACME Corp. Never share credentials."

**Edit Position SOUL:** Organization → Positions → click any position → SOUL tab
- Defines role-specific behavior. The inline editor saves directly to S3.
- Example for SA: "You specialize in AWS architecture. Reference the Well-Architected Framework."

**Edit Personal SOUL:** Organization → Employees → click employee → "Edit SOUL" button
- Opens the full SOUL Editor for that specific employee's agent.

### 4.4 Assign Skills to Positions

Go to **Skill Market** (sidebar):

1. Review the "How Skills Work" guide at the top
2. Click any skill (e.g. `aws-bedrock-kb-search`)
3. **Step 1 — Prerequisites:** Check if API keys are needed (AWS-native skills show "IAM Role" = no config needed)
4. **Step 2 — Assign to Position:** Select a position → click "Assign"
5. All agents in that position will load the skill at their next session

**Skill types:**
- **AWS-native** (bedrock-kb-search, nova-canvas, ses-mailer): Zero config, IAM role provides access
- **Third-party** (github, jira, firecrawl): Need API key first → API Key Vault tab

### 4.5 Set Up IM Bots

This connects the platform to Telegram, Discord, Slack, etc. so employees can chat with their agents via IM.

#### Step 1: SSM into the gateway EC2

```bash
aws ssm start-session --target <INSTANCE_ID> --region <REGION>
sudo su - ubuntu
```

#### Step 2: Add IM channels using OpenClaw CLI

```bash
# Telegram — get token from @BotFather
openclaw channels add telegram --token "YOUR_TELEGRAM_BOT_TOKEN"

# Discord — get token from discord.com/developers → Bot tab
openclaw channels add discord --token "YOUR_DISCORD_BOT_TOKEN"

# Slack — get token from api.slack.com/apps → Bot User OAuth Token
openclaw channels add slack --bot-token "xoxb-YOUR_TOKEN" --app-token "xapp-YOUR_TOKEN"

# Feishu / Lark — get App ID + Secret from Feishu Admin Console
openclaw channels add feishu --app-id "YOUR_APP_ID" --app-secret "YOUR_APP_SECRET"

# Verify all channels
openclaw channels list
```

Each platform has its own token/credential format. Full channel setup docs: https://docs.openclaw.ai/channels

**Important:** When configuring DM access mode, select **"Open"** (not "Pairing"). The enterprise platform uses H2 Proxy for identity verification — Gateway-level pairing is unnecessary and will block employees.

#### Step 3: Verify in Admin Console

Go back to **Admin Console → IM Channels → Refresh**. Configured channels should show "Bot Active" status.

#### Alternative: Gateway Web UI

If you prefer a browser-based setup wizard:

```bash
# Port-forward to Gateway UI (from your local machine):
aws ssm start-session --target <INSTANCE_ID> --region <REGION> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# Get gateway token:
aws ssm get-parameter \
  --name "/openclaw/<STACK_NAME>/gateway-token" \
  --with-decryption --query Parameter.Value --output text --region <REGION>

# Open: http://localhost:18789/?token=<TOKEN> → Channels → Add
```

The CLI provides the same configuration options as the Web UI. Use whichever you prefer.

#### Channel documentation

Each platform (Telegram, Discord, Slack, WhatsApp, Feishu/Lark, Microsoft Teams, Google Chat) has its own setup process. Refer to the official guides: https://docs.openclaw.ai/channels

#### Verify after setup

```bash
# SSM into EC2
aws ssm start-session --target <INSTANCE_ID> --region <REGION>
sudo su - ubuntu
openclaw channels list
```

After adding, go back to Admin Console → **IM Channels** → click **Refresh** → status should show "Bot Active".

### 4.6 Verify with Playground

Go to **Playground** (sidebar):
1. Select an employee profile (e.g. Carol — Finance Analyst)
2. Send "who are you" → agent should respond with Finance Analyst identity
3. Send "run git status" → Carol should refuse (no shell access)
4. Switch to Ryan (SDE) → "run git status" → should execute (SDE has shell)

This confirms SOUL templates, tool permissions, and Bedrock connectivity are all working.

---

## 5. Employee Onboarding

### For the Admin

1. **Add employee:** Organization → Employees → "Add Employee"
   - Select position (determines SOUL, skills, permissions)
   - A Serverless agent is auto-provisioned
2. **Share credentials:** Employee ID + initial password (`ADMIN_PASSWORD`)
3. **Share Portal URL:** The port-forwarded URL or CloudFront domain

### For the Employee

1. **Log in:** Open Portal URL → enter Employee ID + initial password
2. **Set personal password:** First login requires setting a new personal password (min 8 chars, uppercase, lowercase, digit, special character). Cannot proceed until password is changed.
3. **Chat:** Click "Chat" in sidebar → start talking to your agent
4. **Connect IM (optional):**
   - Portal → "Connect IM" → Select platform (Telegram, Discord, etc.)
   - Follow the pairing instructions (scan QR or send `/start` token to the bot)
   - Admin approves pairing in Admin Console → Bindings → "Approve Pairing"
5. **Profile:** Portal → "My Profile" → view agent details, SOUL version, active skills

---

## 6. Troubleshooting

### Services Won't Start

```bash
# Check individual service logs
journalctl -u openclaw-admin --since "5 min ago" --no-pager | tail -30
journalctl -u tenant-router --since "5 min ago" --no-pager | tail -30
journalctl -u bedrock-proxy-h2 --since "5 min ago" --no-pager | tail -30
journalctl -u openclaw-gateway --since "5 min ago" --no-pager | tail -30
```

### DynamoDB "ResourceNotFoundException"

**Symptom:** Login fails, API returns 401, logs show `ResourceNotFoundException`.

**Cause:** DYNAMODB_TABLE doesn't match the actual table name.

**Fix:** Check `/etc/openclaw/env` — `DYNAMODB_TABLE` must equal `STACK_NAME`.
```bash
grep DYNAMODB_TABLE /etc/openclaw/env
grep STACK_NAME /etc/openclaw/env
# These values MUST match
```

### Bedrock Returns Empty Response

**Symptom:** Playground chat returns blank or "I couldn't process your request."

**Causes:**
1. Model not enabled in Bedrock console
2. VPC endpoint misconfigured (if using private endpoints)

**Fix:**
```bash
# Test Bedrock access from EC2
aws bedrock-runtime invoke-model \
  --model-id "minimax.minimax-m2.5" \
  --body '{"messages":[{"role":"user","content":[{"text":"hello"}]}]}' \
  --region us-east-1 /dev/stdout 2>&1 | head -5
```

### Admin Console Returns 404

**Symptom:** `http://localhost:8099` shows 404 or blank page.

**Cause:** Frontend dist/ not built or not copied to /opt/admin-console/dist/.

**Fix:**
```bash
ls /opt/admin-console/dist/
# Should contain: index.html, assets/
# If empty, rebuild:
su - ubuntu -c "source ~/.nvm/nvm.sh && cd /opt/admin-console && npx vite build"
```

### Gateway UI Won't Load

**Symptom:** `http://localhost:18789` times out.

**Causes:**
1. Port-forward not active
2. OpenClaw gateway service not running

**Fix:**
```bash
systemctl status openclaw-gateway
# If inactive:
systemctl restart openclaw-gateway
```

### IM Bot Not Responding

**Symptom:** Employee messages the bot, no response.

**Causes:**
1. Bot token invalid or revoked
2. H2 Proxy not running (port 8091)
3. Tenant Router not running (port 8090)
4. Employee not paired (no MAPPING record in DynamoDB)

**Fix:**
```bash
# Check all services
systemctl is-active openclaw-gateway tenant-router bedrock-proxy-h2
# Check if employee is bound
curl -s "http://localhost:8099/api/v1/internal/im-binding-check?channel=telegram&channelUserId=<USER_ID>"
```

### "Missing workspace template: AGENTS.md"

**Symptom:** `openclaw tui` on EC2 fails with this error.

**Cause:** OpenClaw workspace templates not in `$HOME/docs/reference/templates/`.

**Fix:** This only affects `openclaw tui` (the local terminal UI on EC2). The enterprise platform's agent runtime (AgentCore) uses a different code path and is not affected. If you need `openclaw tui` for testing:
```bash
# Copy templates from npm package
OPENCLAW_PKG=$(node -e "console.log(require.resolve('openclaw/package.json').replace('/package.json',''))")
cp -r "$OPENCLAW_PKG/docs" ~/docs
```

---

## 7. Architecture Quick Reference

```
Employee (Telegram/Discord/Slack/Portal)
  ↓
EC2 Gateway Instance
  ├── OpenClaw Gateway (18789) — IM connections, session management
  ├── Bedrock H2 Proxy (8091) — intercepts API calls, fast-path routing
  ├── Tenant Router (8090) — maps employee → agent → runtime
  └── Admin Console (8099) — admin UI, Portal, API
  ↓
AgentCore Runtime (Firecracker microVM per employee session)
  ├── Workspace Assembler — merges 3-layer SOUL + skills + KB
  └── OpenClaw Agent — processes message → Bedrock → response
  ↓
Amazon Bedrock (model inference)
```

**Ports:** 8090 (router), 8091 (proxy), 8099 (admin), 18789 (gateway)

**Data stores:** DynamoDB (org data, audit), S3 (workspaces, SOUL, skills), SSM (secrets, config), EFS (always-on persistence)

**4-Tier Runtime Model (production):**

| Tier | Model | Guardrail | Positions |
|------|-------|-----------|-----------|
| Standard | MiniMax M2.5 | Moderate (PII) | AE, CSM, HR, PM |
| Restricted | DeepSeek V3.2 | Strict (topic + PII) | FA, Legal |
| Engineering | Claude Sonnet 4.5 | None | SDE, DevOps, QA |
| Executive | Claude Sonnet 4.6 | None | Exec, SA |

**Key Architecture Decisions:**
- **No Session Storage** — every cold start rebuilds workspace from S3. Eliminates identity loss, stale KB, 1GB space risk.
- **Dual mode** — AgentCore (serverless, cold start 25s) or Fargate (always-on, 0s cold start). Per-position toggle.
- **Agent knows its S3 path** — workspace_assembler injects S3 bucket/path into SOUL.md context, enabling autonomous file upload.
- **ThreadingMixIn** — server.py + tenant_router.py use multi-threaded HTTP server. Eliminates 502 from healthcheck blocking.

---

## 8. Cost Estimates

| Component | Monthly Cost | Notes |
|-----------|-------------|-------|
| EC2 c7g.large | ~$60 | Admin Console + Gateway (IM relay) |
| Bedrock (4-tier models) | ~$2-15/employee | MiniMax M2.5 cheapest, Sonnet 4.6 most expensive |
| DynamoDB | ~$5 | On-demand, scales with usage |
| S3 | ~$1 | Workspace storage |
| AgentCore (serverless) | Usage-based | Per-session, cold start 25s |
| Fargate (always-on) | ~$16-31/tier/month | 4 tiers ≈ $73/month, 0s cold start |
| EFS | ~$0.30/GB/month | Fargate workspace persistence |
| **Total (30 emp, AgentCore)** | **~$80/month** | Serverless mode |
| **Total (30 emp, Fargate)** | **~$145/month** | Always-on mode, instant response |

---

## 9. Cleanup

To delete all resources:

```bash
# Delete CloudFormation stack (EC2, VPC, S3, ECR, ECS, EFS)
aws cloudformation delete-stack --stack-name <STACK_NAME> --region <REGION>

# Delete DynamoDB table (if in different region)
aws dynamodb delete-table --table-name <STACK_NAME> --region <DYNAMODB_REGION>

# Delete SSM parameters
aws ssm delete-parameters --names \
  "/openclaw/<STACK_NAME>/admin-password" \
  "/openclaw/<STACK_NAME>/jwt-secret" \
  "/openclaw/<STACK_NAME>/gateway-token" \
  "/openclaw/<STACK_NAME>/runtime-id" \
  --region <REGION>
```
