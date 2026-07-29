# =============================================================================
# Shared race-simulator container image.
#
# Built and pushed ONCE here; every attendee's ECS task definition (../aws)
# references this image by its immutable digest tag, so no attendee needs a
# local Docker build.
# =============================================================================

locals {
  datagen_path = "${path.module}/../../datagen"
  # Immutable tag derived from the build inputs, so a mid-workshop rebuild
  # produces a new tag instead of silently moving :latest under running tasks.
  image_tag = substr(sha1(join("", [
    filesha1("${path.module}/../../datagen/Dockerfile"),
    filesha1("${path.module}/../../datagen/requirements.txt"),
    filesha1("${path.module}/../../datagen/simulator.py"),
    filesha1("${path.module}/../../datagen/config.py"),
    filesha1("${path.module}/../../datagen/race_script.py"),
    filesha1("${path.module}/../../datagen/telemetry.py"),
    filesha1("${path.module}/../../datagen/drivers.py"),
  ])), 0, 12)
}

resource "aws_ecr_repository" "simulator" {
  name         = "${lower(var.prefix)}-simulator"
  force_delete = true

  tags = {
    owner_email = var.owner_email
  }
}

resource "null_resource" "docker_build_push" {
  triggers = {
    image_tag = local.image_tag
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      if ! docker info >/dev/null 2>&1; then
        echo "Error: Docker is not ready. Start Docker Desktop or Colima, verify with 'docker info', then retry." >&2
        exit 1
      fi
      aws ecr get-login-password --region ${var.region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.simulator.repository_url}
      docker build --platform linux/amd64 -t ${aws_ecr_repository.simulator.repository_url}:${local.image_tag} ${local.datagen_path}
      docker push ${aws_ecr_repository.simulator.repository_url}:${local.image_tag}
    EOT
  }

  depends_on = [aws_ecr_repository.simulator]
}
