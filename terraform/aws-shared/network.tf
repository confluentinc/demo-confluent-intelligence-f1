# Dedicated VPC for the attendee ECS simulator tasks.
#
# Public subnets + an internet gateway only (no NAT): the tasks need outbound
# access to Confluent Cloud and ECR and take a public IP for it, as they did in
# the default VPC. Nothing listens inbound — the per-attendee security group is
# egress-only. The Postgres host is not moved here; see modules/postgres.
data "aws_availability_zones" "available" {
  state = "available"

  # Standard AZs only: opted-in Local/Wavelength Zones are returned otherwise
  # and reject subnet attributes Fargate needs.
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  simulator_azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "simulator" {
  cidr_block           = var.simulator_vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = "${local.name_prefix}-simulator"
    owner_email = var.owner_email
  }
}

resource "aws_internet_gateway" "simulator" {
  vpc_id = aws_vpc.simulator.id

  tags = {
    Name        = "${local.name_prefix}-simulator"
    owner_email = var.owner_email
  }
}

resource "aws_subnet" "simulator" {
  count = length(local.simulator_azs)

  vpc_id            = aws_vpc.simulator.id
  availability_zone = local.simulator_azs[count.index]
  cidr_block        = cidrsubnet(var.simulator_vpc_cidr, 8, count.index)
  # No map_public_ip_on_launch: the ECS service sets assign_public_ip itself.

  tags = {
    Name        = "${local.name_prefix}-simulator-${local.simulator_azs[count.index]}"
    owner_email = var.owner_email
  }
}

resource "aws_route_table" "simulator" {
  vpc_id = aws_vpc.simulator.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.simulator.id
  }

  tags = {
    Name        = "${local.name_prefix}-simulator"
    owner_email = var.owner_email
  }
}

resource "aws_route_table_association" "simulator" {
  count = length(aws_subnet.simulator)

  subnet_id      = aws_subnet.simulator[count.index].id
  route_table_id = aws_route_table.simulator.id
}
