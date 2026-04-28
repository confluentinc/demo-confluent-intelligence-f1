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

resource "aws_security_group" "mq" {
  name_prefix = "f1-demo-mq-"
  description = "Security group for IBM MQ + race simulator"

  ingress {
    from_port   = 1414
    to_port     = 1414
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "IBM MQ listener"
  }

  ingress {
    from_port   = 9443
    to_port     = 9443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "IBM MQ web console"
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
    Name        = "f1-demo-mq"
    owner_email = var.owner_email
  }
}

resource "aws_instance" "mq" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.mq.id]
  key_name               = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = file("${path.module}/user_data.sh")

  tags = {
    Name        = "f1-demo-mq"
    owner_email = var.owner_email
  }
}
