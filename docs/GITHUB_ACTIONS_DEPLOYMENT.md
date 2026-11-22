# GitHub Actions to EC2 with Amazon ECR

GitHub Actions verifies the project, builds an immutable ARM64 image, pushes it
to the Terraform-managed private ECR repository, then tells EC2 to pull and run
that image through Systems Manager Run Command. AWS access uses GitHub OIDC;
there are no Docker Hub credentials or static AWS keys in GitHub.

## One-time AWS setup

The Terraform configuration must set:

```hcl
github_actions_repository = "Sidhartha97/radiologyos"
```

The testing host is enabled by `testing-host.auto.tfvars`. Apply the reviewed
Terraform changes from PowerShell:

```powershell
$env:AWS_PROFILE = "radiology-dev"
Set-Location "C:\path\to\radiology-reporting\terraform"
terraform plan -var-file="historical-reports.tfvars" -out="ecr-cicd.tfplan"
terraform show "ecr-cicd.tfplan"
# Confirm the plan updates the GitHub Actions role and removes only the obsolete
# Docker Hub pull secret, then apply that reviewed plan.
terraform apply "ecr-cicd.tfplan"
```

This enables the GitHub OIDC role to push only to this app's ECR repository and
send SSM deployment commands to this EC2 instance. EC2 already has scoped ECR
pull access. The obsolete Docker Hub pull secret is removed by Terraform.

## Initial application bootstrap

Run the explicit initial database/schema bootstrap and first ECR deployment
from the project root. The RDS instance must be running:

```powershell
Set-Location "C:\path\to\radiology-reporting"
py -3.12 scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema --admin-email sidharthachintu1997@gmail.com
```

This command builds and pushes the first image to ECR, applies the approved
database migrations, configures the application, and starts the EC2 service.
It does not run Terraform.

## GitHub Actions configuration

In the GitHub repository, open **Settings → Secrets and variables → Actions →
Variables** and add these repository variables:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | `us-east-1` |
| `AWS_ROLE_ARN` | `terraform output -raw github_actions_deploy_role_arn` |
| `EC2_INSTANCE_ID` | `terraform output -raw testing_instance_id` (single-host mode) |
| `ECR_REPOSITORY_URI` | `terraform output -raw application_repository_url` |
| `AUTOSCALING_GROUP_NAME` | Optional: `terraform output -raw application_asg_name`; leave blank for the existing single-host deployment |
| `APP_IMAGE_PARAMETER` | Optional: `terraform output -raw application_image_parameter_name` |
| `APP_TARGET_GROUP_ARN` | Optional: `terraform output -raw application_target_group_arn` |

No Docker Hub secrets or AWS access keys are required. The workflow uses
GitHub's OIDC identity to assume the role limited to this repository's `main`
branch.

## Deploy changes

Commit and push to `main`, or run **Actions → CI and EC2 deployment → Run
workflow** on `main`. Pull requests run verification only. A successful main
branch run builds and pushes an ARM64 image tagged with the commit SHA. With
`AUTOSCALING_GROUP_NAME` unset it deploys through SSM to the single EC2 host.
When the optional ALB/ASG Terraform mode is enabled and all three ASG variables
are set, the workflow updates the image parameter, starts a rolling ASG instance
refresh and waits for an ALB healthy target. The ASG launch template fetches the
image parameter and scoped app secrets at instance boot.

Open the app at the current testing IP from the single allowed client IP:

```powershell
terraform -chdir=terraform output -raw testing_instance_public_ip
```

Use `http://<that-address>:8000/` only with synthetic data and test accounts.
For real clinical data, configure HTTPS and complete a security review first.
