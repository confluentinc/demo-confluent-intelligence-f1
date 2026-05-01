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

resource "aws_security_group" "postgres" {
  name_prefix = "${lower(var.name_prefix)}-postgres-"
  description = "Security group for Postgres"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "PostgreSQL"
  }

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access"
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
    driver_race_history_seed_b64 = base64gzip(file("${path.module}/../../../data/driver_race_history_seed.sql"))
  })

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = {
    Name        = "${lower(var.name_prefix)}-postgres"
    owner_email = var.owner_email
  }
}
