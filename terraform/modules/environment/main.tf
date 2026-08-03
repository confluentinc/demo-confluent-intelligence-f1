terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

resource "confluent_environment" "main" {
  display_name = var.environment_name

  stream_governance {
    package = "ESSENTIALS"
  }
}

# Console access for the attendee. Everything else in this workshop authenticates
# with the service account's API keys (modules/cluster) — this is the one binding
# for a *human* principal, so a logged-in attendee sees their environment and can
# submit Flink statements from the browser SQL workspace instead of an empty org.
data "confluent_user" "attendee" {
  count = var.grant_console_access ? 1 : 0
  email = var.attendee_email
}

resource "confluent_role_binding" "attendee_env_admin" {
  count       = var.grant_console_access ? 1 : 0
  principal   = "User:${data.confluent_user.attendee[0].id}"
  role_name   = "EnvironmentAdmin"
  crn_pattern = confluent_environment.main.resource_name
}
