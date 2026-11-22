# Single-node development host. Public HTTP ingress is closed unless the
# operator sets testing_app_client_cidr to one current IPv4 /32.
# Set false only when intentionally removing the testing-host resources.
enable_testing_host   = true
testing_instance_type = "t4g.micro"
