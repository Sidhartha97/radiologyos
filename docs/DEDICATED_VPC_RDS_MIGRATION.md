# Dedicated VPC and RDS migration

This change is Terraform-only. It creates a dedicated VPC with two public ALB subnets, two private application subnets, and two isolated database subnets. The ALB and Auto Scaling Group move to this VPC. A single NAT Gateway provides outbound access for private app instances; its Elastic IP is for NAT egress. The ALB uses AWS-managed addresses and its DNS name, so an ALB Elastic IP is not applicable.

The existing RDS database stays in the default VPC as the rollback source. RDS cannot be moved between VPCs in place, so the migration creates a manual snapshot and restores a second database into the private database subnets. The source is retained. The restored database has deletion protection enabled.

## Before migration

1. Use a maintenance window. Stop report edits/uploads and other database writes. The snapshot captures a point in time; later writes will not be in the restored copy.
2. Confirm the `dedicated_vpc_cidr` (`10.40.0.0/16`) does not overlap any connected office/VPN network.
3. In `terraform/testing-host.auto.tfvars`, keep `autoscaling_min_size = 0` and `enable_rds_vpc_migration = false` for the first apply. This stops the app ASG while the network is being moved.
4. From PowerShell in `terraform/`, run:

   ```powershell
   terraform plan -out vpc-stage.tfplan
   terraform show vpc-stage.tfplan
   ```

   Confirm that the plan does not destroy `aws_db_instance.postgres`, its subnet group, S3, Cognito, DynamoDB, or Bedrock resources. The single EC2 deployment host is replaced to attach it to the dedicated VPC; its attached Elastic IP is retained if AWS permits the reassociation. No RDS snapshot or clone is created in this first stage.
5. If that review is clean, apply the reviewed file:

   ```powershell
   terraform apply vpc-stage.tfplan
   ```

## Snapshot and restore RDS

1. In AWS Console, set the Auto Scaling Group's desired capacity to `0` (keep min size at `0`) and wait until it has no instances. The Terraform resource intentionally ignores desired-capacity drift, so this operational step is required. Stop `radiologyos.service` on the SSM deployment host using Systems Manager Run Command. Keep all application writes paused.
2. Set `enable_rds_vpc_migration = true` in `testing-host.auto.tfvars`.
3. Plan and inspect the migration stage:

   ```powershell
   terraform plan -out rds-vpc-migration.tfplan
   terraform show rds-vpc-migration.tfplan
   ```

   Expect one manual snapshot and one additional RDS instance. The existing source database must show no destroy or replacement. Applying creates a second billable RDS instance and snapshot storage.
4. Apply only after confirming the plan:

   ```powershell
   terraform apply rds-vpc-migration.tfplan
   ```

Terraform outputs `postgres_endpoint`, `postgres_secret_arn`, and `postgres_database` for the restored instance after this stage. `source_postgres_endpoint` continues to identify the original rollback database.

## Configure and bring the app back

The application code is unchanged. The runtime database secret must be refreshed so it points at the restored RDS endpoint and contains a valid runtime login. From the project root, deploy the current approved image/schema with the existing deployment helper:

```powershell
py -3.12 scripts/deploy_aws.py deploy --profile radiology-dev --approve-schema
```

This helper uses the SSM deployment host in the dedicated VPC and Terraform outputs. Keep the ASG at zero until the helper completes and `/api/health` succeeds on that host. Then:

1. Set `autoscaling_min_size = 1` in `testing-host.auto.tfvars`.
2. Run `terraform plan`, review it, then apply. The ASG starts from the currently configured immutable ECR image and reads the refreshed application configuration/runtime database secret.
3. Open `application_alb_dns_name` and verify login, report read/write, historical library access, and RAG retrieval. Compare expected records in the restored database before resuming normal use.
4. Keep the original RDS and `rds_migration_snapshot_id` until all checks pass. The old database is the rollback target. Do not destroy it as part of this migration.

## Costs and rollback

The dedicated VPC creates a NAT Gateway and Elastic IP, which have ongoing hourly/data charges. The migration temporarily runs two RDS instances and stores a snapshot. This development topology uses one NAT Gateway and is not highly available for egress. The ASG maximum remains four.

If validation fails, keep writes paused, point the application runtime secret back to the source DB via the deployment helper, and leave the source RDS intact. Do not destroy the source until the migrated app is accepted. To later remove all infrastructure, deliberately remove the source/destination `prevent_destroy` lifecycle guards, disable destination RDS deletion protection, and review both migration snapshots before destroying anything.
