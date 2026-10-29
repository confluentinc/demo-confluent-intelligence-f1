# The root's wiring of each developer's stack: one module instance per email,
# statements in their own environment's catalog, run as the key's owner.
# `terraform test` (Terraform >= 1.7 for mock_provider). Every provider is
# mocked, so nothing here talks to Confluent Cloud or sleeps.

mock_provider "confluent" {
  mock_data "confluent_user" {
    defaults = {
      id = "u-dev01"
    }
  }

  # The role binding validates crn_pattern, and the statements rest_endpoint,
  # so random mock strings won't do.
  mock_resource "confluent_environment" {
    defaults = {
      resource_name = "crn://confluent.cloud/organization=mock-org/environment=env-mock01"
    }
  }

  mock_data "confluent_flink_region" {
    defaults = {
      rest_endpoint = "https://flink.us-east-1.aws.confluent.cloud"
    }
  }
}

# The key's owner differs from the developers' user ID, so a statement running
# as the developer would fail the principal check.
mock_provider "http" {
  mock_data "http" {
    defaults = {
      status_code   = 200
      response_body = jsonencode({ spec = { owner = { id = "u-abc123", kind = "User" } } })
    }
  }
}

mock_provider "random" {}

mock_provider "time" {}

variables {
  confluent_cloud_api_key    = "MOCKGLOBALKEY"
  confluent_cloud_api_secret = "mock-global-secret"
  developer_emails           = ["jane.doe+envadmin@example.com", "John.Doe@example.com"]
}

# Applies, not plans: before Terraform 1.11 (override_during) an overridden
# value stays unknown at plan time.
run "two_developers_get_two_stacks" {
  override_resource {
    target = random_id.env_suffix
    values = { hex = "7f3a" }
  }

  assert {
    condition     = length(module.developer) == 2
    error_message = "expected one module.developer instance per email"
  }

  assert {
    condition = (
      module.developer["jane.doe+envadmin@example.com"].environment_name == "jane-doe-envadmin-7f3a_env" &&
      module.developer["John.Doe@example.com"].environment_name == "John-Doe-7f3a_env"
    )
    error_message = "each developer should get their own <sanitized>-<hex>_env environment"
  }
}

run "statements_use_their_own_environment_as_catalog" {
  override_resource {
    target = random_id.env_suffix
    values = { hex = "7f3a" }
  }

  assert {
    condition     = alltrue([for m in module.developer : length(m.flink_statements) == 5])
    error_message = "expected five statements per developer: three tables, the seed and the model"
  }

  assert {
    condition = alltrue(flatten([
      for email, name in {
        "jane.doe+envadmin@example.com" = "jane-doe-envadmin-7f3a_env"
        "John.Doe@example.com"             = "John-Doe-7f3a_env"
      } :
      [for s in values(module.developer[email].flink_statements) : s.catalog == name && s.database == "RIVER-RACING-DEMO-CLUSTER"]
    ]))
    error_message = "every statement's catalog should be its own developer's environment, and its database RIVER-RACING-DEMO-CLUSTER"
  }
}

run "statements_run_as_the_key_owner" {
  assert {
    condition     = local.principal_id == "u-abc123"
    error_message = "principal_id should be spec.owner.id from GET /iam/v2/api-keys/<key>"
  }

  assert {
    condition = alltrue(flatten([
      for m in module.developer : [for s in values(m.flink_statements) : s.principal == "u-abc123"]
    ]))
    error_message = "every statement should run as the platform key's owner, not as the developer"
  }

  assert {
    condition = (
      nonsensitive(data.http.key_owner.url) == "https://api.confluent.cloud/iam/v2/api-keys/MOCKGLOBALKEY" &&
      nonsensitive(data.http.key_owner.request_headers["Authorization"]) == "Basic ${base64encode("MOCKGLOBALKEY:mock-global-secret")}"
    )
    error_message = "the lookup should ask about the platform key, authenticated with that key"
  }
}

run "non_200_lookup_fails" {
  command = plan

  override_data {
    target = data.http.key_owner
    values = {
      status_code   = 401
      response_body = jsonencode({ error_code = 401, message = "Unable to authenticate" })
    }
  }

  expect_failures = [data.http.key_owner]
}
