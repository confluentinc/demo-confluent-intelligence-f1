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
  name_prefix = "f1-demo-${var.deployment_id}-postgres-"
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
    Name        = "f1-demo-${var.deployment_id}-postgres"
    owner_email = var.owner_email
  }
}

resource "aws_instance" "postgres" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.postgres.id]
  key_name               = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = file("${path.module}/user_data.sh")

  tags = {
    Name        = "f1-demo-${var.deployment_id}-postgres"
    owner_email = var.owner_email
  }
}
