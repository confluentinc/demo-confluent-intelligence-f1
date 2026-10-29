# Apply-time checks of the root: real random suffixes and the handout table.
# `terraform test` (Terraform >= 1.7 for mock_provider). Random runs locally, so
# the suffix is a genuine random_id; confluent, http and time are mocked. The
# per-developer resources are checked in developer_module.tftest.hcl.

mock_provider "confluent" {
  mock_data "confluent_user" {
    defaults = {
      id = "u-mock01"
    }
  }

  mock_resource "confluent_environment" {
    defaults = {
      id            = "env-mock01"
      resource_name = "crn://confluent.cloud/organization=mock-org/environment=env-mock01"
    }
  }

  mock_data "confluent_flink_region" {
    defaults = {
      rest_endpoint = "https://flink.us-east-1.aws.confluent.cloud"
    }
  }
}

mock_provider "http" {
  mock_data "http" {
    defaults = {
      status_code   = 200
      response_body = jsonencode({ spec = { owner = { id = "u-abc123", kind = "User" } } })
    }
  }
}

mock_provider "time" {}

variables {
  confluent_cloud_api_key    = "mock-key"
  confluent_cloud_api_secret = "mock-secret"
  developer_emails           = ["jane.doe+envadmin@example.com", "John.Doe@example.com"]
}

run "creates_one_environment_per_developer" {
  assert {
    condition     = can(regex("^jane-doe-envadmin-[0-9a-f]{4}_env$", module.developer["jane.doe+envadmin@example.com"].environment_name))
    error_message = "expected a name matching jane-doe-envadmin-[0-9a-f]{4}_env"
  }

  assert {
    condition     = can(regex("^John-Doe-[0-9a-f]{4}_env$", module.developer["John.Doe@example.com"].environment_name))
    error_message = "expected a name matching John-Doe-[0-9a-f]{4}_env"
  }

  # Sorted by email (uppercase first), header on top, columns padded to line up.
  assert {
    condition = output.handout == join("\n", [
      "EMAIL                          ENVIRONMENT                 ENVIRONMENT ID  CONSOLE URL",
      "John.Doe@example.com           John-Doe-${random_id.env_suffix["John.Doe@example.com"].hex}_env           env-mock01      https://confluent.cloud/environments/env-mock01",
      "jane.doe+envadmin@example.com  jane-doe-envadmin-${random_id.env_suffix["jane.doe+envadmin@example.com"].hex}_env  env-mock01      https://confluent.cloud/environments/env-mock01",
      "",
    ])
    error_message = "handout should be an aligned table: email, environment name, environment ID, Console URL"
  }
}
