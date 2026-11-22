# Optional single-node testing host. No NAT, ALB or SSH keys. A temporary
# web ingress can be enabled for one explicitly approved /32 client address.
variable "enable_testing_host" {
  type    = bool
  default = false
}

variable "enable_alb_autoscaling" {
  type        = bool
  default     = false
  description = "Create an HTTP Application Load Balancer and an ARM Auto Scaling Group for the optional testing host. Adds ongoing AWS cost."
}

variable "autoscaling_min_size" {
  type    = number
  default = 1
}

variable "autoscaling_max_size" {
  type    = number
  default = 2
}

variable "autoscaling_cpu_target" {
  type    = number
  default = 60
}

variable "testing_app_client_cidr" {
  type        = string
  default     = ""
  description = "Optional temporary HTTP app access from one test PC IPv4 /32. Leave empty for no public web access."
  validation {
    condition     = var.testing_app_client_cidr == "" || (can(cidrnetmask(var.testing_app_client_cidr)) && can(regex("/32$", var.testing_app_client_cidr)))
    error_message = "testing_app_client_cidr must be empty or one IPv4 /32 address. Do not use 0.0.0.0/0."
  }
}

variable "github_actions_repository" {
  type        = string
  default     = ""
  description = "GitHub owner/repository allowed to deploy from the main branch, e.g. owner/radiology-reporting."
  validation {
    condition     = var.github_actions_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_actions_repository))
    error_message = "Use a GitHub owner/repository value or leave blank to disable GitHub deployment access."
  }
}

variable "testing_instance_type" {
  type    = string
  default = "t4g.micro"
  validation {
    condition     = contains(["t4g.micro", "t4g.small"], var.testing_instance_type)
    error_message = "Use the approved ARM testing sizes; review costs before increasing."
  }
}

data "aws_ami" "testing" {
  count       = var.enable_testing_host ? 1 : 0
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.1-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

data "aws_subnet" "testing_candidates" {
  for_each = var.enable_testing_host ? toset(data.aws_subnets.default.ids) : toset([])
  id       = each.value
}

locals {
  testing_name    = "${var.project_name}-${var.environment}"
  testing_subnets = sort([for subnet in data.aws_subnet.testing_candidates : subnet.id])
  # Keep the runtime role separate from the database operator/master identity.
  testing_base_statements = [for statement in jsondecode(aws_iam_role_policy.application.policy).Statement : statement
  if !contains(statement.Action, "secretsmanager:GetSecretValue")]
}

resource "aws_security_group" "load_balancer" {
  count       = var.enable_testing_host && var.enable_alb_autoscaling && !var.enable_dedicated_vpc ? 1 : 0
  name        = "${local.testing_name}-alb"
  description = "HTTP ingress to the test application load balancer"
  vpc_id      = data.aws_vpc.default.id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "load_balancer_http" {
  count             = var.enable_testing_host && var.enable_alb_autoscaling && !var.enable_dedicated_vpc && var.testing_app_client_cidr != "" ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.load_balancer[0].id
  protocol          = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_blocks       = [var.testing_app_client_cidr]
  description       = "Test HTTP access from the configured client /32 only"
}

resource "aws_security_group_rule" "application_from_alb" {
  count                    = var.enable_testing_host && var.enable_alb_autoscaling && !var.enable_dedicated_vpc ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.application.id
  source_security_group_id = aws_security_group.load_balancer[0].id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  description              = "Application traffic only from the ALB"
}

resource "aws_ssm_parameter" "application_image" {
  count       = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name        = "/${local.testing_name}/application-image"
  description = "Current immutable RadiologyOS ECR image URI for ASG instances"
  type        = "String"
  value       = "bootstrap"
  lifecycle { ignore_changes = [value] }
}

resource "aws_lb" "application" {
  count              = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name               = substr("${local.testing_name}-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.enable_dedicated_vpc ? aws_security_group.load_balancer_dedicated[0].id : aws_security_group.load_balancer[0].id]
  subnets            = var.enable_dedicated_vpc ? aws_subnet.public[*].id : data.aws_subnets.default.ids
  tags               = { Name = "${local.testing_name}-alb" }
  lifecycle {
    precondition {
      condition     = var.testing_app_client_cidr != ""
      error_message = "Set testing_app_client_cidr to one IPv4 /32 before enabling the public HTTP ALB."
    }
  }
}

resource "aws_lb_target_group" "application" {
  count       = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name        = substr("${local.testing_name}-app", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.enable_dedicated_vpc ? aws_vpc.dedicated[0].id : data.aws_vpc.default.id
  target_type = "instance"
  health_check {
    enabled             = true
    path                = "/api/health"
    protocol            = "HTTP"
    matcher             = "200-399"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  count             = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  load_balancer_arn = aws_lb.application[0].arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.application[0].arn
  }
}

resource "aws_launch_template" "application" {
  count                  = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name_prefix            = "${local.testing_name}-asg-"
  image_id               = data.aws_ami.testing[0].id
  instance_type          = var.testing_instance_type
  update_default_version = true
  vpc_security_group_ids = [var.enable_dedicated_vpc ? aws_security_group.application_dedicated[0].id : aws_security_group.application.id]
  iam_instance_profile { name = aws_iam_instance_profile.testing_host[0].name }
  user_data = base64encode(templatefile("${path.module}/../deployment/asg_bootstrap.sh.tftpl", {
    region            = var.aws_region
    config_secret_arn = aws_secretsmanager_secret.application_config[0].arn
    image_parameter   = aws_ssm_parameter.application_image[0].name
  }))
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 8
      encrypted             = true
      delete_on_termination = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.testing_name}-app-asg" }
  }
  depends_on = [aws_iam_role_policy.testing_runtime]
}

resource "aws_autoscaling_group" "application" {
  count                     = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name                      = "${local.testing_name}-application"
  min_size                  = var.autoscaling_min_size
  max_size                  = var.autoscaling_max_size
  desired_capacity          = var.autoscaling_min_size
  vpc_zone_identifier       = var.enable_dedicated_vpc ? aws_subnet.application_private[*].id : data.aws_subnets.default.ids
  target_group_arns         = [aws_lb_target_group.application[0].arn]
  health_check_type         = "ELB"
  health_check_grace_period = 600
  launch_template {
    id      = aws_launch_template.application[0].id
    version = "$Latest"
  }
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      instance_warmup        = 600
    }
  }
  tag {
    key                 = "Name"
    value               = "${local.testing_name}-application-asg"
    propagate_at_launch = true
  }
  lifecycle { ignore_changes = [desired_capacity] }
  depends_on = [aws_lb_listener.http]
}

resource "aws_autoscaling_policy" "cpu_target" {
  count                     = var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name                      = "${local.testing_name}-cpu-target"
  autoscaling_group_name    = aws_autoscaling_group.application[0].name
  policy_type               = "TargetTrackingScaling"
  estimated_instance_warmup = 300
  target_tracking_configuration {
    predefined_metric_specification { predefined_metric_type = "ASGAverageCPUUtilization" }
    target_value = var.autoscaling_cpu_target
  }
}

resource "aws_secretsmanager_secret" "runtime" {
  count                   = var.enable_testing_host ? 1 : 0
  name                    = "${local.testing_name}/application-postgres"
  description             = "Radiology OS dedicated non-owner application PostgreSQL credentials"
  kms_key_id              = aws_kms_key.radiology.arn
  recovery_window_in_days = 0
  # Password versions are populated by explicit reviewed bootstrap, not state.
}

resource "aws_secretsmanager_secret" "application_config" {
  count                   = var.enable_testing_host ? 1 : 0
  name                    = "${local.testing_name}/application-config"
  kms_key_id              = aws_kms_key.radiology.arn
  recovery_window_in_days = 0
}

resource "aws_ecr_repository" "application" {
  count                = var.enable_testing_host ? 1 : 0
  name                 = "${local.testing_name}-application"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # Explicit terraform destroy of this test deployment.
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "application" {
  count      = var.enable_testing_host ? 1 : 0
  repository = aws_ecr_repository.application[0].name
  policy = jsonencode({ rules = [{
    rulePriority = 1
    description  = "Keep the ten latest testing builds"
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
    action       = { type = "expire" }
  }] })
}

data "aws_iam_policy_document" "runtime_extras" {
  count = var.enable_testing_host ? 1 : 0
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.runtime[0].arn, aws_secretsmanager_secret.application_config[0].arn]
  }
  statement {
    actions   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.radiology.arn]
  }
  statement {
    actions   = ["cognito-idp:AdminDeleteUser"]
    resources = [aws_cognito_user_pool.radiology.arn]
  }
  statement {
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = [for prefix in ["historical-originals", "old-reports", "final-reports"] : "${aws_s3_bucket.documents.arn}/${prefix}/*"]
  }
  statement {
    actions   = ["s3:ListBucketVersions"]
    resources = [aws_s3_bucket.documents.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["historical-originals/*", "old-reports/*", "final-reports/*"]
    }
  }
  statement {
    actions   = ["bedrock:DeleteKnowledgeBaseDocuments"]
    resources = [aws_bedrockagent_knowledge_base.radiology.arn]
  }
}

# Import the previously approved AWS-only policy when enabling existing infra.
resource "aws_iam_role_policy" "runtime_extras" {
  count  = var.enable_testing_host ? 1 : 0
  name   = "radiologyos-approved-runtime-access"
  role   = aws_iam_role.application.id
  policy = data.aws_iam_policy_document.runtime_extras[0].json
}

resource "aws_iam_role" "testing_host" {
  count = var.enable_testing_host ? 1 : 0
  name  = "${local.testing_name}-testing-host"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_iam_instance_profile" "testing_host" {
  count = var.enable_testing_host ? 1 : 0
  name  = "${local.testing_name}-testing-host"
  role  = aws_iam_role.testing_host[0].name
}

resource "aws_iam_role_policy_attachment" "testing_ssm" {
  count      = var.enable_testing_host ? 1 : 0
  role       = aws_iam_role.testing_host[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_security_group_rule" "testing_web" {
  count             = var.enable_testing_host && !var.enable_alb_autoscaling && !var.enable_dedicated_vpc && var.testing_app_client_cidr != "" ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.application.id
  protocol          = "tcp"
  from_port         = 8000
  to_port           = 8000
  cidr_blocks       = [var.testing_app_client_cidr]
  description       = "Temporary plain-HTTP testing access from the configured PC only"
}

resource "aws_security_group_rule" "testing_web_dedicated" {
  count             = var.enable_testing_host && var.enable_dedicated_vpc && !var.enable_alb_autoscaling && var.testing_app_client_cidr != "" ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.application_dedicated[0].id
  protocol          = "tcp"
  from_port         = 8000
  to_port           = 8000
  cidr_blocks       = [var.testing_app_client_cidr]
  description       = "Temporary plain-HTTP testing access from the configured PC only"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  count          = var.enable_testing_host && var.github_actions_repository != "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github_actions_deploy" {
  count = var.enable_testing_host && var.github_actions_repository != "" ? 1 : 0
  name  = "${local.testing_name}-github-actions-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github_actions[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike = {
          # GitHub repositories created after 2026-07-15 include immutable
          # owner/repository IDs in sub. Keep the legacy form for older repos.
          "token.actions.githubusercontent.com:sub" = [
            "repo:${var.github_actions_repository}:ref:refs/heads/main",
            "repo:${try(split("/", var.github_actions_repository)[0], "")}@*/${try(split("/", var.github_actions_repository)[1], "")}@*:ref:refs/heads/main"
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  count = var.enable_testing_host && var.github_actions_repository != "" ? 1 : 0
  name  = "${local.testing_name}-github-actions-deploy"
  role  = aws_iam_role.github_actions_deploy[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = aws_ecr_repository.application[0].arn
      },
      {
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          aws_instance.testing[0].arn,
          "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}::document/AWS-RunShellScript"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = var.enable_alb_autoscaling ? aws_ssm_parameter.application_image[0].arn : "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/disabled"
      },
      {
        Effect   = "Allow"
        Action   = ["autoscaling:StartInstanceRefresh", "autoscaling:DescribeInstanceRefreshes", "autoscaling:DescribeAutoScalingGroups", "autoscaling:SetDesiredCapacity", "elasticloadbalancing:DescribeTargetHealth"]
        Resource = var.enable_alb_autoscaling ? aws_autoscaling_group.application[0].arn : "arn:${data.aws_partition.current.partition}:autoscaling:${var.aws_region}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/disabled"
      }
    ]
  })
}

resource "aws_iam_role_policy" "testing_runtime" {
  count = var.enable_testing_host ? 1 : 0
  role  = aws_iam_role.testing_host[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(
    local.testing_base_statements,
    jsondecode(data.aws_iam_policy_document.runtime_extras[0].json).Statement,
    [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload"], Resource = aws_ecr_repository.application[0].arn },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.documents.arn}/deployment-bundles/*" },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = var.enable_alb_autoscaling ? aws_ssm_parameter.application_image[0].arn : "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/disabled" }
    ]
  ) })
}

resource "aws_instance" "testing" {
  count                       = var.enable_testing_host ? 1 : 0
  ami                         = data.aws_ami.testing[0].id
  instance_type               = var.testing_instance_type
  subnet_id                   = var.enable_dedicated_vpc ? aws_subnet.public[0].id : try(local.testing_subnets[0], null)
  associate_public_ip_address = true # Outbound SSM/ECR without a paid NAT gateway.
  vpc_security_group_ids      = [var.enable_dedicated_vpc ? aws_security_group.application_dedicated[0].id : aws_security_group.application.id]
  iam_instance_profile        = aws_iam_instance_profile.testing_host[0].name
  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/../deployment/ec2_bootstrap.sh.tftpl", {
    region            = var.aws_region
    config_secret_arn = aws_secretsmanager_secret.application_config[0].arn
  })
  credit_specification { cpu_credits = "standard" }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 8
    encrypted             = true
    delete_on_termination = true
  }
  tags = { Name = "${local.testing_name}-testing-host" }
  lifecycle {
    precondition {
      condition     = var.enable_dedicated_vpc || length(local.testing_subnets) > 0
      error_message = "No usable subnet for the testing host; review the network plan."
    }
  }
  depends_on = [aws_iam_role_policy.testing_runtime, aws_iam_role_policy_attachment.testing_ssm]
}

resource "aws_eip" "testing" {
  count    = var.enable_testing_host && var.testing_app_client_cidr != "" ? 1 : 0
  domain   = "vpc"
  instance = aws_instance.testing[0].id
  tags     = { Name = "${local.testing_name}-testing-web" }
}

output "testing_instance_id" { value = try(aws_instance.testing[0].id, null) }
output "testing_instance_public_ip" { value = try(aws_eip.testing[0].public_ip, null) }
output "testing_host_role_name" { value = try(aws_iam_role.testing_host[0].name, null) }
output "application_repository_url" { value = try(aws_ecr_repository.application[0].repository_url, null) }
output "github_actions_deploy_role_arn" { value = try(aws_iam_role.github_actions_deploy[0].arn, null) }
output "runtime_database_secret_arn" { value = try(aws_secretsmanager_secret.runtime[0].arn, null) }
output "application_config_secret_arn" { value = try(aws_secretsmanager_secret.application_config[0].arn, null) }

