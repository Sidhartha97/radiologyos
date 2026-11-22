# Development infrastructure proposal. Applying creates chargeable services.
aws_region            = "us-east-1"
documents_bucket_name = "radiologyos-dev-540581804458-reports"
vector_bucket_name    = "radiologyos-dev-540581804458-vectors"
# Alternative small instance class after db.t4g.micro capacity failure.
db_instance_class = "db.t3.micro"
# EC2 connects to RDS through the VPC security group; keep the database private.
db_publicly_accessible    = false
dev_client_cidr           = ""
github_actions_repository = "Sidhartha97/radiologyos"
testing_app_client_cidr   = "49.37.115.195/32"