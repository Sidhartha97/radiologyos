# GitHub Actions → ECR → EC2 Auto Scaling deployment

The workflow in `.github/workflows/deploy.yml` verifies changes and delivers ARM64 Docker images to the EC2 Auto Scaling Group behind the ALB.

## Workflow behavior

- Pull requests run backend tests and a frontend production build; they do not deploy.
- Pushes to `main` and manual `workflow_dispatch` runs on `main` can deploy.
- GitHub Actions assumes a narrowly scoped AWS role using OIDC. Do not create GitHub secrets containing long-lived AWS keys.
- Buildx pushes an immutable image tagged with commit SHA, workflow run ID and attempt number. A unique attempt suffix lets reruns coexist with ECR tag immutability.
- The ASG deployment branch writes the image URI to SSM Parameter Store, starts an instance refresh, waits for completion and checks ALB target health.
- The legacy single-instance branch uses SSM Run Command when `AUTOSCALING_GROUP_NAME` is empty. Use ASG variables for the current ALB/ASG deployment.

## Required GitHub repository variables

Add these under **Repository Settings → Secrets and variables → Actions → Variables**. Use Terraform outputs as the source of truth; `.env` does not populate GitHub variables.

| Variable | Current purpose |
|---|---|
| `AWS_REGION` | Deployment region, currently `us-east-1` |
| `AWS_ROLE_ARN` | `terraform output -raw github_actions_deploy_role_arn` |
| `ECR_REPOSITORY_URI` | `terraform output -raw application_repository_url` |
| `AUTOSCALING_GROUP_NAME` | `terraform output -raw application_asg_name`; must be non-empty to select ASG rollout |
| `APP_IMAGE_PARAMETER` | `terraform output -raw application_image_parameter_name` |
| `APP_TARGET_GROUP_ARN` | `terraform output -raw application_target_group_arn` |

`EC2_INSTANCE_ID` is only needed for legacy single-instance mode. Do not leave the ASG group variable blank when intending to deploy behind the ALB.

## First-time database bootstrap

After Terraform provisions a fresh RDS database, run the reviewed bootstrap from the project root. It applies the versioned schema, creates/rotates the non-owner runtime login when explicitly requested, updates runtime configuration and starts the deployment host service:

```powershell
py -3.12 scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema --admin-email admin@example.com
```

Use a verified Cognito administrator address for `--admin-email`. This step is needed after a fresh database so the administrator identity is bound in the new schema. Keep the email and database outputs private if appropriate for the account.

## Deploy a code change

```powershell
git add <changed-files>
git commit -m "Describe the change"
git push origin main
```

Open **GitHub → Actions → CI and EC2 deployment**. Verify the verification job, ARM64 build/ECR push, ASG refresh and ALB health check. The application URL is the current `application_alb_dns_name` Terraform output; the development listener is HTTP and should only be used with synthetic data.

## IAM and troubleshooting

- OIDC assumption failures: verify `AWS_ROLE_ARN`, repository identity, allowed `main` ref and `id-token: write` permission.
- ECR push denied: inspect the Terraform-managed GitHub deploy role policy and apply its reviewed IAM update.
- Instance refresh permission denied: the deploy role needs refresh start, scoped to the ASG, plus describe operations (some describe APIs require `Resource: "*"`).
- Target health permission denied: `elasticloadbalancing:DescribeTargetHealth` requires `Resource: "*"` under AWS service authorization.
- No ASG deployment step: check `AUTOSCALING_GROUP_NAME`, `APP_IMAGE_PARAMETER`, and `APP_TARGET_GROUP_ARN` variables.
- Unhealthy targets: inspect the target group health reason, EC2 service status through SSM, application configuration secret and RDS connectivity. Do not paste secret values or clinical text into issue reports.
