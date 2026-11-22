# EC2 testing deployment and recreation

This is the approved low-cost, single-node **testing** setup, not production
clinical hosting. RAG prompts, model selection, retrieval and rendering remain
unchanged. Subscriptions remain off; manual payments are records only.

## Open the app (Windows)

For the direct public-IP testing mode, set `testing_app_client_cidr` in
`terraform/historical-reports.tfvars` to your current public IP as `/32`, apply
Terraform, then get `terraform output -raw testing_instance_public_ip` and open
`http://<ip>:8000/`. This is plain HTTP restricted to that one IP. Use synthetic
reports and test credentials only. See `GITHUB_ACTIONS_DEPLOYMENT.md` for the
setup. If the ingress is unset, there is no public web access and the SSM method
below remains available.

1. Install AWS's Session Manager plugin using the official installer:
   https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin-windows.html
   Download `SessionManagerPluginSetup.exe`, run it with administrator approval,
   keep the default installation location, then reopen PowerShell.
2. Verify the plugin and your existing AWS profile:

   ```powershell
   session-manager-plugin
   aws sts get-caller-identity --profile radiology-dev
   ```

   If the first command is not found, reopen PowerShell or run
   `& "$env:ProgramFiles\Amazon\SessionManagerPlugin\bin\session-manager-plugin.exe"`.
   The project tunnel helper also checks the standard Windows installation
   locations and supplies the plugin directory to AWS CLI's child PATH. A missing
   PATH entry alone no longer requires reinstalling or editing the system PATH.
3. From the `radiology-reporting` project directory, keep this command running:

   ```powershell
   python scripts/deploy_aws.py tunnel --profile radiology-dev
   ```

4. Open http://127.0.0.1:18000/ and use your Cognito email/password. Closing the
   tunnel terminal stops this access, not EC2 billing.

The tunnel is encrypted by Systems Manager. Public-IP mode is plain HTTP for
temporary single-IP synthetic-data tests only. This is not public HTTPS; a real
production domain/TLS and secure-cookie configuration remain separate work.

## Deploy updated code

Local prerequisites: Python, boto3 (`python -m pip install boto3`), Terraform,
AWS CLI and access to the approved AWS account/profile. Local Docker access is
not required: the ARM image is built on the dedicated EC2 host and pushed to ECR.

Review source/migrations first, then explicitly approve schema bootstrap:

```powershell
python scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema
python scripts/deploy_aws.py status --profile radiology-dev
```

No DDL runs automatically at app startup. The explicit bootstrap reuses the
dedicated runtime password and grants only reviewed runtime access. An operator
temporarily grants the testing-host role access to the master secret for this
step, removing it in `finally`. The normal testing-host role cannot read the
RDS-managed master secret. If deployment is interrupted, inspect/remove the
`radiologyos-bootstrap-temporary` role policy before leaving the host running.

The script reads **current Terraform outputs**, creates the application config
secret version, and uses instance-role credentials. It never packages `.env`,
AWS keys, Terraform state, uploads or historical reports into the image. The
runtime database password stays in Secrets Manager, outside Terraform state.
There is no need to hand-edit regenerated IDs in `.env` for EC2. Local Docker
still uses its own `.env` and must be updated separately if used later.

The 1-GiB host has limited build capacity. Source bundles and Docker build caches
consume disk over time; inspect disk before many repeated deployments. Do not
upgrade the instance or remove cached artifacts without reviewing cost/targets.

## Recreate after an intentional teardown

This procedure recreates infrastructure/code, **not deleted data**. A new RDS
database, Cognito pool and knowledge base start empty. Old customer passwords,
reports, historical files and vectors do not survive complete destruction.

```powershell
$env:AWS_PROFILE = 'radiology-dev'
Set-Location terraform
terraform init
terraform plan '-var-file=historical-reports.tfvars' '-out=recreate.tfplan'
# Review before apply: no unintended deletes/replacements of other projects.
terraform apply recreate.tfplan
Set-Location ..
python scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema --admin-email sidharthachintu1997@gmail.com
python scripts/deploy_aws.py tunnel --profile radiology-dev
```

`terraform/testing-host.auto.tfvars` enables this project's testing host.
Bootstrap creates a fresh non-owner database login/secret when both are absent.
Existing credentials are not silently rotated. A fresh Cognito pool requires a
new invitation and private password/email verification. The approved email gets
platform-admin access only, not clinical/signing permission. Create customer
accounts and re-upload historical reports after signing in.

## Costs and teardown cautions

Incremental estimated hosting at 730 hours: about US$10.50–12/month, excluding
existing RDS, Bedrock, S3/S3 Vectors, DynamoDB, taxes and extra traffic. The host
uses t4g.micro, standard CPU credits, encrypted 8-GiB gp3 and one outbound IPv4.
No NAT gateway, load balancer, inbound ports or high availability are added.

Stopping EC2 stops compute/associated public-IP charges but not EBS, ECR,
Secrets Manager or the other services. A complete reviewed Terraform destroy is
required to remove managed resources. **No destroy was requested or performed
by this implementation.** Versioned document buckets intentionally refuse to
destroy while nonempty (`force_destroy=false`); inspect/explicitly authorize
emptying all versions first when teardown is actually requested. Deployment
bundles also count as bucket objects. Do not assume a failed destroy removed all
billable resources. Terraform state must remain available and private for later
management; never commit `.env`, state or plan files to Git.

## Permission-save and permanent customer deletion

Sensitive administrative changes require a fresh login. A new identity-confirm
dialog accepts the current password/MFA and retries the rejected change once,
preserving the form. It never asks to paste credentials into chat.

Customer accounts expose **Permanently delete** with a count preview, reason and
explicit Yes/No. Confirmation immediately suspends access. A 2-hour quarantine
allows previously signed upload links and in-flight imports to expire. Cleanup
then verifies exact tenant S3 object versions, Bedrock documents, DynamoDB items
and exclusive Cognito identities before purging SQL records/signed history.
Shared users and AWS infrastructure remain. Staff are only deactivated.

Failures show a safe stage/error code and allow retry; incomplete cleanup is
never displayed as complete. A minimal opaque cleanup receipt and DynamoDB block
marker remain to prevent re-import. No real customer was deleted for testing.

## Remaining gates

Browser verification through the user's installed tunnel plugin is still needed.
Do not use real patient data yet. Public TLS/domain, private RDS posture, backup
and restore drills, MFA policy, durable generation/publication workflows and full
security/load/clinical validation remain production gates. Single-node testing
deployment and configuration checks do not satisfy those gates.
