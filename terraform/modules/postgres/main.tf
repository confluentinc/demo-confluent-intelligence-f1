data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "aws_security_group" "postgres" {
  name_prefix = "${lower(var.name_prefix)}-postgres-"
  description = "Security group for Postgres"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    # Confluent's managed CDC connector runs outside this AWS account and must
    # reach the shared host over its public address. This is intentionally open
    # at the network layer; the generated database password remains required.
    description = "PostgreSQL for managed CDC connector (public reachability required)"
  }

  dynamic "ingress" {
    for_each = toset(var.ssh_ingress_cidr)

    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
      description = "SSH access"
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${lower(var.name_prefix)}-postgres"
    owner_email = var.owner_email
  }
}

resource "aws_instance" "postgres" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.postgres.id]
  key_name               = var.key_pair_name != "" ? var.key_pair_name : null

  # Seed SQL is injected as gzip+base64 (EC2 user_data has a 16KB limit;
  # the 198-row driver_race_history seed exceeds that uncompressed).
  user_data = templatefile("${path.module}/user_data.sh", {
    driver_race_history_seed_b64 = base64gzip(file("${path.module}/../../../datagen/data/driver_race_history_seed.sql"))
    max_replication_slots        = var.max_replication_slots
    postgres_password            = random_password.postgres.result
  })

  # A password rotation must replace and reseed the instance. Do not enable
  # user_data_replace_on_change: routine attendee-count changes must not
  # replace a live shared database. See the migration runbook for the explicit
  # replacement required when changing other boot-time settings.
  lifecycle {
    replace_triggered_by = [random_password.postgres]
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = {
    Name        = "${lower(var.name_prefix)}-postgres"
    owner_email = var.owner_email
  }
}
