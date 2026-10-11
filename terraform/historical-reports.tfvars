# Development infrastructure proposal. Applying creates chargeable services.
aws_region            = "us-east-1"
documents_bucket_name = "radiologyos-dev-540581804458-reports"
vector_bucket_name    = "radiologyos-dev-540581804458-vectors"
# Alternative small instance class after db.t4g.micro capacity failure.
db_instance_class = "db.t3.micro"
# Local testing proposal: PostgreSQL access only from this PC's public IPv4.
# Update this /32 if the ISP changes the address. Restore private access for hosting.
db_publicly_accessible = true
dev_client_cidr        = "49.37.115.84/32"
