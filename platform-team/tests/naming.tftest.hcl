# Checks of environment naming and the developer_emails validations.
# `terraform test` (Terraform >= 1.7 for mock_provider). The confluent, http and
# time providers are mocked, so nothing here talks to Confluent Cloud or sleeps.

mock_provider "confluent" {
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
}

# Keep identical to SANITIZER_EXAMPLES in tests/test_preprovisioned_lookup.py
# (the Python twin, sanitize_local_part in scripts/preprovisioned/lookup.py).
run "sanitizer_examples" {
  command = plan

  variables {
    developer_emails = [
      "jane.doe+envadmin@example.com",
      "John.Doe@acme.com",
      "o'brien@x.com",
      "a..b@x",
      "_x_@y",
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbb@x.com",
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbb@x.com",
    ]
  }

  assert {
    condition     = local.sanitized["jane.doe+envadmin@example.com"] == "jane-doe-envadmin"
    error_message = "jane.doe+envadmin@example.com should sanitize to jane-doe-envadmin"
  }

  assert {
    condition     = local.sanitized["John.Doe@acme.com"] == "John-Doe"
    error_message = "John.Doe@acme.com should sanitize to John-Doe (case kept)"
  }

  assert {
    condition     = local.sanitized["o'brien@x.com"] == "o-brien"
    error_message = "o'brien@x.com should sanitize to o-brien"
  }

  assert {
    condition     = local.sanitized["a..b@x"] == "a-b"
    error_message = "a..b@x should sanitize to a-b (runs of - collapse)"
  }

  assert {
    condition     = local.sanitized["_x_@y"] == "x"
    error_message = "_x_@y should sanitize to x (- and _ trimmed from both ends)"
  }

  # 60-character local part: cut to 40, which here ends on a letter.
  assert {
    condition = (
      length(split("@", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbb@x.com")[0]) == 60 &&
      local.sanitized["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbb@x.com"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    error_message = "a 60-character local part should be cut to its first 40 characters"
  }

  # 60-character local part whose 40th character is the "-" from ".": the trim
  # runs after the cut, so no trailing "-" survives (39 characters).
  assert {
    condition = (
      length(split("@", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbb@x.com")[0]) == 60 &&
      local.sanitized["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbb@x.com"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    error_message = "a cut landing on - should leave no trailing - (39 characters)"
  }
}

# An apply, not a plan: before Terraform 1.11 (override_during) an overridden
# value stays unknown at plan time.
run "environment_names_with_a_fixed_suffix" {
  variables {
    developer_emails = ["jane.doe+envadmin@example.com", "John.Doe@example.com"]
  }

  override_resource {
    target = random_id.env_suffix
    values = { hex = "7f3a" }
  }

  assert {
    condition     = module.developer["jane.doe+envadmin@example.com"].environment_name == "jane-doe-envadmin-7f3a_env"
    error_message = "expected jane-doe-envadmin-7f3a_env"
  }

  assert {
    condition     = module.developer["John.Doe@example.com"].environment_name == "John-Doe-7f3a_env"
    error_message = "expected John-Doe-7f3a_env"
  }
}

run "case_insensitive_duplicates_fail" {
  command = plan

  variables {
    developer_emails = ["a@x.com", "A@x.com"]
  }

  expect_failures = [var.developer_emails]
}

run "email_with_nothing_to_name_an_environment_fails" {
  command = plan

  variables {
    developer_emails = ["+++@x"]
  }

  expect_failures = [var.developer_emails]
}
