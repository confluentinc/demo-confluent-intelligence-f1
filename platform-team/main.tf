# =============================================================================
# Platform team: everything each developer's environment needs, one stack per
# developer (modules/developer): a new, empty environment with a direct
# EnvironmentAdmin role binding for the developer's own user account, a Kafka
# cluster, a Flink compute pool, and the workshop's tables, seed and model.
#
# Run with a Global key owned by an OrganizationAdmin. It creates no service
# accounts and no API keys: that one key is the provider credential and signs
# every Flink statement. Developers only mint their own keys and write a
# credential card (`uv run deploy --track pre-provisioned`).
# =============================================================================

locals {
  emails = toset(var.developer_emails)

  # Environment name stem: the email's local part, made safe for a Confluent
  # display name ([A-Za-z0-9_-], starting and ending alphanumeric):
  #   1. take everything before the first "@";
  #   2. replace every character outside [A-Za-z0-9_-] with "-";
  #   3. collapse runs of "-" into one;
  #   4. cut to 40 characters;
  #   5. trim "-" and "_" from both ends.
  # Case is kept. The developer's deploy finds its environment by this name, so
  # the rule has a Python twin, sanitize_local_part in
  # scripts/preprovisioned/lookup.py. Change both together, and keep their
  # example lists identical (tests/naming.tftest.hcl here,
  # tests/test_preprovisioned_lookup.py there). The empty-name validation on
  # developer_emails (variables.tf) inlines this expression twice.
  sanitized = {
    for email in local.emails :
    email => trim(substr(replace(replace(split("@", email)[0], "/[^A-Za-z0-9_-]/", "-"), "/-+/", "-"), 0, 40), "-_")
  }

  # The key's own owner. An OrganizationAdmin can't create keys for, or run
  # statements as, another user, so every statement runs as the platform team.
  principal_id = jsondecode(data.http.key_owner.response_body).spec.owner.id
}

data "http" "key_owner" {
  url = "https://api.confluent.cloud/iam/v2/api-keys/${var.confluent_cloud_api_key}"
  request_headers = {
    Accept        = "application/json"
    Authorization = "Basic ${base64encode("${var.confluent_cloud_api_key}:${var.confluent_cloud_api_secret}")}"
  }

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "GET /iam/v2/api-keys/<key> returned ${self.status_code}: confluent_cloud_api_key or its secret is wrong, or the key has expired or been deleted."
    }
  }
}

# A missing user fails the plan. Developers must already be in the org (SSO
# first sign-in or an accepted invitation).
data "confluent_user" "developer" {
  for_each = local.emails
  email    = each.key
}

# 4 lowercase hex characters, kept in state, so re-applying never renames an environment.
resource "random_id" "env_suffix" {
  for_each    = local.emails
  byte_length = 2
}

# Removing an email destroys that developer's whole stack, their work included.
module "developer" {
  source   = "./modules/developer"
  for_each = local.emails

  user_id                    = data.confluent_user.developer[each.key].id
  environment_name           = "${local.sanitized[each.key]}-${random_id.env_suffix[each.key].hex}_env"
  region                     = var.region
  flink_max_cfu              = var.flink_max_cfu
  csp_hosted_model           = var.csp_hosted_model
  principal_id               = local.principal_id
  confluent_cloud_api_key    = var.confluent_cloud_api_key
  confluent_cloud_api_secret = var.confluent_cloud_api_secret
}
