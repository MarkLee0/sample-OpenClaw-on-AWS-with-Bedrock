# E2E Test Walkthrough — Ready to Run

> Stack: `openclaw-e2e-test` | Region: `us-west-2` | EC2: `i-036bfe702e14e2866`

---

## 1. Access Admin Console

```bash
# Terminal 1: Port forward (keep open)
aws ssm start-session --target i-036bfe702e14e2866 --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8099"],"localPortNumber":["8099"]}'
```

Open browser: http://localhost:8099

Login:
- Employee ID: `emp-jiade`
- Password: `E2eTest2026!`

---

## 2. Set Up IM Bots (via CLI on EC2)

SSM into the EC2 first:
```bash
aws ssm start-session --target i-036bfe702e14e2866 --region us-west-2
```

Then switch to ubuntu user and configure IM channels:
```bash
sudo su - ubuntu

# Add Telegram bot
openclaw channels add telegram --token "YOUR_TELEGRAM_BOT_TOKEN"

# Add Discord bot
openclaw channels add discord --token "YOUR_DISCORD_BOT_TOKEN"

# Add Slack bot
openclaw channels add slack --token "xoxb-YOUR_SLACK_BOT_TOKEN"

# Add Feishu bot
openclaw channels add feishu --app-id "YOUR_APP_ID" --app-secret "YOUR_APP_SECRET"

# Verify all channels
openclaw channels list

# Restart gateway to pick up changes
systemctl --user restart openclaw-gateway
```

**Where to get bot tokens:**
- **Telegram:** Message @BotFather on Telegram → `/newbot` → copy token
- **Discord:** https://discord.com/developers/applications → New App → Bot → Copy Token
- **Slack:** https://api.slack.com/apps → Create App → Bot User OAuth Token
- **Feishu:** Feishu Admin Console → create bot → App ID + App Secret

**Alternative: Gateway Web UI** (if you prefer a GUI)
```bash
# Terminal 2: Port forward
aws ssm start-session --target i-036bfe702e14e2866 --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# Get token
aws ssm get-parameter \
  --name "/openclaw/openclaw-e2e-test/gateway-token" \
  --with-decryption --query Parameter.Value --output text --region us-west-2

# Open: http://localhost:18789/?token=<paste token>
# → Channels → Add bot
```

---

## 3. SSM Shell (Direct EC2 Access)

```bash
aws ssm start-session --target i-036bfe702e14e2866 --region us-west-2
```

Check services:
```bash
systemctl is-active openclaw-admin tenant-router bedrock-proxy-h2 openclaw-gateway
ss -tlnp | grep -E '8090|8091|8099|18789'
```

Check config:
```bash
cat /etc/openclaw/env
```

Check DynamoDB:
```bash
curl -s http://localhost:8099/api/v1/org/employees | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Employees: {len(d)}')"
```

Check S3 templates:
```bash
aws s3 ls s3://openclaw-e2e-test-263168716248/_shared/soul/global/ --region us-west-2
```

---

## 4. Admin Day-1 Checklist

| # | Task | Where |
|---|------|-------|
| 1 | Login as admin | http://localhost:8099 → emp-jiade / E2eTest2026! |
| 2 | Check Dashboard | Sidebar → Dashboard |
| 3 | Review org structure | Sidebar → Organization → Departments / Positions / Employees |
| 4 | Edit Global SOUL | Sidebar → Security Center → Policies tab → "Edit Global SOUL" |
| 5 | Edit Position SOUL | Organization → Positions → click any position → SOUL tab → Edit |
| 6 | Assign a skill | Sidebar → Skill Market → click a skill → Assign to Position |
| 7 | Test Playground | Sidebar → Playground → select employee → send message |
| 8 | Set up IM bot | Gateway UI (localhost:18789) → Channels → Add bot token |
| 9 | Check IM status | Admin Console → IM Channels → Refresh |

---

## 5. Employee Portal Test

```bash
# Same port forward as Admin Console (port 8099)
```

Open browser: http://localhost:8099/portal

Login as any employee:
- `emp-carol` / `E2eTest2026!` (Finance Analyst)
- `emp-ryan` / `E2eTest2026!` (Software Engineer)
- `emp-sarah` / `E2eTest2026!` (Solutions Architect)

Test:
- Portal → Chat → "who are you?"
- Portal → My Profile
- Portal → My Skills
- Portal → Connect IM (if IM bot configured)

---

## 6. Key Resources

| Resource | Value |
|----------|-------|
| Stack | `openclaw-e2e-test` |
| Region | `us-west-2` |
| EC2 Instance | `i-036bfe702e14e2866` |
| S3 Bucket | `openclaw-e2e-test-263168716248` |
| DynamoDB Table | `openclaw-e2e-test` (us-west-2) |
| AgentCore Runtime | `openclaw_e2e_test_runtime-u8Gvk2AWBV` |
| ECR | `263168716248.dkr.ecr.us-west-2.amazonaws.com/openclaw-e2e-test-agent-container` |
| Admin URL | http://localhost:8099 (via SSM port-forward) |
| Gateway URL | http://localhost:18789/?token=\<from SSM\> (via SSM port-forward) |
| Admin Login | emp-jiade / E2eTest2026! |

---

## 7. Cleanup (After Testing)

```bash
# Delete CloudFormation stack
aws cloudformation delete-stack --stack-name openclaw-e2e-test --region us-west-2

# Delete DynamoDB table
aws dynamodb delete-table --table-name openclaw-e2e-test --region us-west-2

# Delete SSM parameters
aws ssm delete-parameters --names \
  "/openclaw/openclaw-e2e-test/admin-password" \
  "/openclaw/openclaw-e2e-test/jwt-secret" \
  "/openclaw/openclaw-e2e-test/gateway-token" \
  "/openclaw/openclaw-e2e-test/runtime-id" \
  --region us-west-2

# Delete AgentCore Runtime (optional)
aws bedrock-agentcore-control delete-agent-runtime \
  --agent-runtime-id openclaw_e2e_test_runtime-u8Gvk2AWBV \
  --region us-west-2
```
