# Testing the Enterprise Deployment

## Prerequisites

- AWS CLI v2 with SSM Session Manager plugin installed
- Active CloudFormation stack in the target region
- EC2 instance ID from stack outputs

## Access Admin Console

```bash
aws ssm start-session --target <INSTANCE_ID> --region <REGION> \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=8099,localPortNumber=8099'
```

Open http://localhost:8099

- Login with any seeded employee ID (e.g. `emp-carol`)
- Password: the `ADMIN_PASSWORD` you set in `.env`
- First login requires setting a personal password

## Access OpenClaw Gateway Console

```bash
aws ssm start-session --target <INSTANCE_ID> --region <REGION> \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=18789,localPortNumber=18789'
```

Open http://localhost:18789 to configure IM bot channels (Telegram, Slack, Feishu, Discord).

## Key Environment Info

All values come from CloudFormation stack outputs:

```bash
# Get all outputs
aws cloudformation describe-stacks --stack-name <STACK_NAME> --region <REGION> \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' --output table
```

| Resource | How to find |
|----------|-------------|
| EC2 Instance | Stack output `InstanceId` |
| S3 Bucket | Stack output `TenantWorkspaceBucketName` |
| ECR Repo | Stack output `MultitenancyEcrRepositoryUri` |
| AgentCore Runtime | SSM param `/openclaw/<STACK_NAME>/runtime-id` |

## Verify Services on EC2

```bash
# SSH via SSM
aws ssm start-session --target <INSTANCE_ID> --region <REGION>

# Check services
systemctl status openclaw-admin
systemctl status openclaw-tenant-router
systemctl status openclaw-bedrock-proxy-h2

# View logs
journalctl -u openclaw-admin -f --no-pager -n 50
```

## Deploy Updates

```bash
cd enterprise

# Full update (rebuild Docker + redeploy services)
bash deploy.sh --skip-seed

# Update services only (skip Docker rebuild)
bash deploy.sh --skip-build --skip-seed

# Update infrastructure only
bash deploy.sh --skip-build --skip-seed --skip-services
```
