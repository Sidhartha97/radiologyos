# GitHub Actions to EC2 deployment

This workflow builds the existing Dockerfile on GitHub Actions, pushes an
immutable ARM64 image to a private Docker Hub repository, and asks
the EC2 instance to pull/restart it through Systems Manager Run Command. It does
not require an SSM tunnel or an inbound SSH rule. The EC2 instance continues to
run the application using its instance role and runtime configuration in
Secrets Manager. A separate Docker Hub read-only token is kept in a scoped
Secrets Manager secret for EC2 image pulls.

## Before provisioning

1. Create a **private GitHub repository** and push this project to its `main`
   branch. Do not commit `.env`, Terraform state/plan files, AWS credentials,
   uploads, or real patient documents.
2. Edit `terraform/historical-reports.tfvars` and add the repository in the
   exact `owner/repository` form:

   ```hcl
   github_actions_repository = "YOUR_GITHUB_OWNER/radiology-reporting"
   ```

3. For temporary public-IP testing only, set the testing PC's current public
   IPv4 address as a single `/32`:

   ```hcl
   testing_app_client_cidr = "YOUR_CURRENT_PUBLIC_IP/32"
   ```

   This opens HTTP port 8000 only to that address. Leave it blank to keep all
   public web ingress closed. The app has no public HTTPS/domain setup here; use
   synthetic data and test accounts only over this temporary HTTP endpoint.
4. Create a private Docker Hub repository. Create one Docker Hub token with
   write access for GitHub Actions and a separate read-only token for EC2.
5. The Terraform stack is currently destroyed and the local state has no
   managed resources. Check AWS account/profile, unique S3 names and any KMS
   keys still pending deletion before applying. Terraform apply creates billable
   resources including RDS, KMS, Secrets Manager and EC2.

## Create the infrastructure

From PowerShell:

```powershell
$env:AWS_PROFILE = "radiology-dev"
Set-Location "C:\path\to\radiology-reporting\terraform"
terraform init
terraform plan -var-file="historical-reports.tfvars" -out="recreate.tfplan"
# Review every create/replace/delete before proceeding.
terraform apply "recreate.tfplan"
```

The testing host is enabled by `testing-host.auto.tfvars`. Terraform creates a
GitHub OIDC provider and a role restricted to the repository's `main` branch,
with only SSM deployment permissions. It creates a temporary
single-IP port 8000 ingress rule only when `testing_app_client_cidr` is set.

## First application bootstrap

After the infrastructure is ready, install `boto3` locally and perform the
explicit initial database/schema bootstrap and first image deployment:

```powershell
Set-Location "C:\path\to\radiology-reporting"
python -m pip install boto3
python scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema --admin-email sidharthachintu1997@gmail.com
```

This deploy helper uses ECR and the EC2 instance. It explicitly applies the
reviewed database migrations; it does not run Terraform. The admin invitation
is sent only if the new Cognito pool needs that account. Review its output.

## Configure GitHub Actions

In the GitHub repository, open **Settings → Secrets and variables → Actions →
Variables** and add:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | `us-east-1` |
| `AWS_ROLE_ARN` | `terraform output -raw github_actions_deploy_role_arn` |
| `EC2_INSTANCE_ID` | `terraform output -raw testing_instance_id` |
| `DOCKERHUB_IMAGE` | Docker Hub image name, e.g. `myuser/radiologyos` |
| `DOCKERHUB_PULL_SECRET_ARN` | `terraform output -raw dockerhub_pull_secret_arn` |

Add these **Actions secrets**:

| Secret | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub token with push permission |

After Terraform creates the pull-secret shell, add this JSON as its Secrets
Manager secret value:

```json
{"username":"YOUR_DOCKERHUB_USERNAME","token":"YOUR_READ_ONLY_DOCKERHUB_TOKEN"}
```

Use a separate read-only Docker Hub token for EC2. No static AWS keys are put
in GitHub; AWS authentication uses GitHub OIDC. The workflow file is
`.github/workflows/deploy.yml`.

On each pull request to `main`, it installs the Python dependencies, runs the
existing pytest suite, and builds the frontend. On a push to `main`, after those
steps pass, it builds/pushes an ARM64 Docker Hub image tagged with that commit SHA, then
uses SSM Run Command to pull it on EC2, restart the service, and check the
health endpoint. If the new image fails the health check, the host attempts to
restore the prior image.

## Open the testing site

When the ingress variable is set, get the address with:

```powershell
terraform output -raw testing_instance_public_ip
```

Browse to `http://<that-address>:8000/` from the configured test PC. Its
temporary public IPv4 is allocated as an Elastic IP while the testing ingress
is enabled. If your ISP changes your public IP, update the `/32` in the tfvars,
review and apply a Terraform plan. For production, use a domain with HTTPS,
secure cookies, a security review, and appropriate backup/restore controls
before entering real credentials or clinical data.

The repository currently has no Git remote configured. These files must be
reviewed, committed, and pushed to the actual private GitHub repository before
GitHub Actions can run. A workflow run cannot provision the initial AWS stack;
the reviewed Terraform apply is a separate one-time setup. Before AWS variables
are added, pushes run verification only. After infrastructure/bootstrap and
GitHub variables/secrets are ready, run the workflow manually once from
**Actions → CI and EC2 deployment → Run workflow**; later pushes to `main`
deploy automatically.
