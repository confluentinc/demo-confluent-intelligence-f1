# =============================================================================
# Shared workshop infrastructure — run ONCE by the organizer (via wsa).
#
# Provisions the AWS resources that every attendee reuses:
#   - the default VPC + public subnets (used by attendee ECS simulator tasks)
#   - one Postgres host seeded with driver_race_history, sized and configured
#     with enough replication slots for every attendee's CDC connector
#   - one ECR repository holding the race-simulator image (built once here;
#     per-attendee ECS task definitions just reference it — see datagen.tf)
#
# Per-attendee Confluent Cloud + ECS resources live in ../aws and consume the
# outputs of this layer.
# =============================================================================

locals {
  name_prefix = var.prefix
  # The accelerator supports workshops of up to 95 accounts. Keep the shared
  # host at 105 slots (one per CDC connector plus ten spare) so changing an
  # attendee count does not alter EC2 user_data on a resumed run.
  max_replication_slots = var.postgres_max_replication_slots
}

# Use the account's default VPC + public subnets. Attendee ECS tasks run here
# with public IPs to reach Confluent Cloud; the Postgres host lives here too.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

module "postgres" {
  source                = "../modules/postgres"
  aws_region            = var.region
  owner_email           = var.owner_email
  name_prefix           = local.name_prefix
  instance_type         = var.postgres_instance_type
  max_replication_slots = local.max_replication_slots
  ssh_ingress_cidr      = var.ssh_ingress_cidr
}

# Postgres user_data runs asynchronously after the instance is created. Poll the
# service port until it accepts connections before attendee connectors deploy.
resource "null_resource" "wait_for_postgres" {
  depends_on = [module.postgres]

  provisioner "local-exec" {
    command = <<-EOT
      INSTANCE_ID="${module.postgres.postgres_instance_id}"
      REGION="${var.region}"
      echo "Waiting for Postgres at ${module.postgres.postgres_public_ip}:5432 (instance $INSTANCE_ID)..."
      for i in $(seq 1 80); do
        if nc -z -w5 ${module.postgres.postgres_public_ip} 5432 2>/dev/null; then
          echo "Postgres port open after $((i * 15))s"
          exit 0
        fi
        echo "  attempt $i/80 — retrying in 15s..."
        if [ $((i % 4)) -eq 0 ]; then
          STATE=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
            --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo "unknown")
          if [ "$STATE" = "stopped" ] || [ "$STATE" = "terminated" ]; then
            echo "FATAL: Postgres instance $INSTANCE_ID entered state '$STATE' — aborting" && exit 1
          fi
          CONSOLE=$(aws ec2 get-console-output --region "$REGION" --instance-id "$INSTANCE_ID" \
            --query 'Output' --output text 2>/dev/null || true)
          if echo "$CONSOLE" | grep -qE "no space left on device|needs [0-9]+MB more space|Transaction test error|Failed to run module scripts-user"; then
            echo "FATAL: Postgres EC2 user_data failed. Relevant console lines:"
            echo "$CONSOLE" | grep -E "no space left|more space needed|Transaction test|Failed to run" | head -5
            exit 1
          fi
        fi
        sleep 15
      done
      echo "Timeout: Postgres not ready after 20 minutes" && exit 1
    EOT
  }
}
