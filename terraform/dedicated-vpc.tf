data "aws_availability_zones" "dedicated" {
  state = "available"
}

locals {
  dedicated_azs = slice(data.aws_availability_zones.dedicated.names, 0, 2)
}

resource "aws_vpc" "dedicated" {
  count                = var.enable_dedicated_vpc ? 1 : 0
  cidr_block           = var.dedicated_vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name        = "${var.project_name}-${var.environment}-vpc"
    Environment = var.environment
  }
}

resource "aws_internet_gateway" "dedicated" {
  count  = var.enable_dedicated_vpc ? 1 : 0
  vpc_id = aws_vpc.dedicated[0].id
  tags   = { Name = "${var.project_name}-${var.environment}-igw" }
}

resource "aws_subnet" "public" {
  count                   = var.enable_dedicated_vpc ? 2 : 0
  vpc_id                  = aws_vpc.dedicated[0].id
  availability_zone       = local.dedicated_azs[count.index]
  cidr_block              = cidrsubnet(var.dedicated_vpc_cidr, 8, count.index)
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-${var.environment}-public-${count.index + 1}" }
}

resource "aws_subnet" "application_private" {
  count                   = var.enable_dedicated_vpc ? 2 : 0
  vpc_id                  = aws_vpc.dedicated[0].id
  availability_zone       = local.dedicated_azs[count.index]
  cidr_block              = cidrsubnet(var.dedicated_vpc_cidr, 8, count.index + 10)
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.project_name}-${var.environment}-app-private-${count.index + 1}" }
}

resource "aws_subnet" "database_private" {
  count                   = var.enable_dedicated_vpc ? 2 : 0
  vpc_id                  = aws_vpc.dedicated[0].id
  availability_zone       = local.dedicated_azs[count.index]
  cidr_block              = cidrsubnet(var.dedicated_vpc_cidr, 8, count.index + 20)
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.project_name}-${var.environment}-db-private-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  count  = var.enable_dedicated_vpc ? 1 : 0
  vpc_id = aws_vpc.dedicated[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.dedicated[0].id
  }
  tags = { Name = "${var.project_name}-${var.environment}-public" }
}

resource "aws_route_table_association" "public" {
  count          = var.enable_dedicated_vpc ? 2 : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

# One NAT keeps the test environment's recurring network costs lower. The
# private application subnets use it for ECR, SSM, and Secrets Manager egress.
resource "aws_eip" "dedicated_nat" {
  count  = var.enable_dedicated_vpc ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${var.project_name}-${var.environment}-nat" }
}

resource "aws_nat_gateway" "dedicated" {
  count         = var.enable_dedicated_vpc ? 1 : 0
  allocation_id = aws_eip.dedicated_nat[0].id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.dedicated]
  tags          = { Name = "${var.project_name}-${var.environment}-nat" }
}

resource "aws_route_table" "application_private" {
  count  = var.enable_dedicated_vpc ? 1 : 0
  vpc_id = aws_vpc.dedicated[0].id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.dedicated[0].id
  }
  tags = { Name = "${var.project_name}-${var.environment}-app-private" }
}

resource "aws_route_table_association" "application_private" {
  count          = var.enable_dedicated_vpc ? 2 : 0
  subnet_id      = aws_subnet.application_private[count.index].id
  route_table_id = aws_route_table.application_private[0].id
}

# Database subnets intentionally have no internet route.
resource "aws_route_table" "database_private" {
  count  = var.enable_dedicated_vpc ? 1 : 0
  vpc_id = aws_vpc.dedicated[0].id
  tags   = { Name = "${var.project_name}-${var.environment}-db-private" }
}

resource "aws_route_table_association" "database_private" {
  count          = var.enable_dedicated_vpc ? 2 : 0
  subnet_id      = aws_subnet.database_private[count.index].id
  route_table_id = aws_route_table.database_private[0].id
}

resource "aws_security_group" "application_dedicated" {
  count       = var.enable_dedicated_vpc ? 1 : 0
  name        = "${var.project_name}-${var.environment}-application-vpc"
  description = "Private app instances; inbound HTTP only from the ALB"
  vpc_id      = aws_vpc.dedicated[0].id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-${var.environment}-application-vpc" }
}

resource "aws_security_group" "database_dedicated" {
  count       = var.enable_dedicated_vpc ? 1 : 0
  name        = "${var.project_name}-${var.environment}-postgres-vpc"
  description = "Private PostgreSQL in the dedicated VPC"
  vpc_id      = aws_vpc.dedicated[0].id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-${var.environment}-postgres-vpc" }
}

resource "aws_security_group_rule" "database_from_application_dedicated" {
  count                    = var.enable_dedicated_vpc ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.database_dedicated[0].id
  source_security_group_id = aws_security_group.application_dedicated[0].id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  description              = "PostgreSQL from dedicated application instances"
}

resource "aws_db_subnet_group" "postgres_dedicated" {
  count      = var.enable_dedicated_vpc ? 1 : 0
  name       = "${var.project_name}-${var.environment}-postgres-vpc"
  subnet_ids = aws_subnet.database_private[*].id
  tags       = { Name = "${var.project_name}-${var.environment}-postgres-vpc" }
}

resource "aws_security_group" "load_balancer_dedicated" {
  count       = var.enable_dedicated_vpc && var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  name        = "${var.project_name}-${var.environment}-alb-vpc"
  description = "HTTP ingress to the test application ALB"
  vpc_id      = aws_vpc.dedicated[0].id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-${var.environment}-alb-vpc" }
}

resource "aws_security_group_rule" "load_balancer_http_dedicated" {
  count             = var.enable_dedicated_vpc && var.enable_testing_host && var.enable_alb_autoscaling && var.testing_app_client_cidr != "" ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.load_balancer_dedicated[0].id
  protocol          = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_blocks       = [var.testing_app_client_cidr]
  description       = "Test HTTP access from the configured client /32 only"
}

resource "aws_security_group_rule" "application_from_alb_dedicated" {
  count                    = var.enable_dedicated_vpc && var.enable_testing_host && var.enable_alb_autoscaling ? 1 : 0
  type                     = "ingress"
  security_group_id        = aws_security_group.application_dedicated[0].id
  source_security_group_id = aws_security_group.load_balancer_dedicated[0].id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  description              = "Application traffic only from the ALB"
}

