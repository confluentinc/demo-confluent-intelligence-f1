# modules/developer on its own: what one developer's stack contains, and how
# its statements are signed. `terraform test` (Terraform >= 1.7 for
# mock_provider). Every provider is mocked, so nothing here talks to Confluent
# Cloud or sleeps.

mock_provider "confluent" {
  mock_resource "confluent_environment" {
    defaults = {
      id            = "env-abc123"
      resource_name = "crn://confluent.cloud/organization=mock-org/environment=env-abc123"
    }
  }

  mock_data "confluent_flink_region" {
    defaults = {
      rest_endpoint = "https://flink.eu-central-1.aws.confluent.cloud"
    }
  }
}

mock_provider "time" {}

variables {
  user_id                    = "u-dev01"
  environment_name           = "John-Doe-c21e_env"
  region                     = "eu-central-1"
  flink_max_cfu              = 20
  csp_hosted_model           = "anthropic.claude-sonnet-4-6"
  principal_id               = "u-abc123"
  confluent_cloud_api_key    = "MOCKGLOBALKEY"
  confluent_cloud_api_secret = "mock-global-secret"
}

run "environment_and_direct_binding" {
  module {
    source = "./modules/developer"
  }

  assert {
    condition = (
      confluent_environment.main.display_name == "John-Doe-c21e_env" &&
      confluent_environment.main.stream_governance[0].package == "ADVANCED"
    )
    error_message = "the environment should be named var.environment_name, with Stream Governance ADVANCED"
  }

  assert {
    condition = (
      confluent_role_binding.developer_env_admin.principal == "User:u-dev01" &&
      confluent_role_binding.developer_env_admin.role_name == "EnvironmentAdmin" &&
      confluent_role_binding.developer_env_admin.crn_pattern == confluent_environment.main.resource_name
    )
    error_message = "the developer should be a direct User: EnvironmentAdmin on their own environment"
  }
}

# The developer's deploy finds the cluster and pool by these names.
run "cluster_and_pool" {
  command = plan

  module {
    source = "./modules/developer"
  }

  assert {
    condition = (
      confluent_kafka_cluster.main.display_name == "RIVER-RACING-DEMO-CLUSTER" &&
      confluent_kafka_cluster.main.cloud == "AWS" &&
      confluent_kafka_cluster.main.region == "eu-central-1" &&
      confluent_kafka_cluster.main.availability == "SINGLE_ZONE" &&
      length(confluent_kafka_cluster.main.standard) == 1
    )
    error_message = "expected a Standard single-zone RIVER-RACING-DEMO-CLUSTER on AWS in var.region"
  }

  assert {
    condition = (
      confluent_flink_compute_pool.main.display_name == "RIVER-RACING-DEMO-pool" &&
      confluent_flink_compute_pool.main.cloud == "AWS" &&
      confluent_flink_compute_pool.main.region == "eu-central-1" &&
      confluent_flink_compute_pool.main.max_cfu == 20
    )
    error_message = "expected RIVER-RACING-DEMO-pool on AWS in var.region, with max_cfu = var.flink_max_cfu"
  }
}

run "statements_are_signed_with_the_platform_key" {
  module {
    source = "./modules/developer"
  }

  assert {
    condition = length(regexall(
      "resource\\s+\"confluent_flink_statement\"",
      join("\n", [for f in fileset(path.module, "*.tf") : file("${path.module}/${f}")]),
    )) == 5
    error_message = "Expected exactly five statements (three tables, the seed, the model); a new one must be added to the checks below and get its own targeted-plan run."
  }

  assert {
    condition = alltrue([
      for s in [
        confluent_flink_statement.create_car_telemetry_table,
        confluent_flink_statement.create_race_standings_table,
        confluent_flink_statement.create_driver_race_history_table,
        confluent_flink_statement.seed_driver_race_history,
        confluent_flink_statement.llm_textgen_model,
      ] :
      nonsensitive(s.credentials[0].key) == "MOCKGLOBALKEY" &&
      nonsensitive(s.credentials[0].secret) == "mock-global-secret" &&
      s.principal[0].id == "u-abc123" &&
      s.rest_endpoint == "https://flink.eu-central-1.aws.confluent.cloud" &&
      s.properties["sql.current-catalog"] == "John-Doe-c21e_env" &&
      s.properties["sql.current-database"] == "RIVER-RACING-DEMO-CLUSTER"
    ])
    error_message = "every statement should be signed with the Global key, run as var.principal_id, on the region's endpoint, in the environment's catalog and the cluster's database"
  }

  assert {
    condition = alltrue([
      for s in [
        confluent_flink_statement.create_car_telemetry_table,
        confluent_flink_statement.create_race_standings_table,
        confluent_flink_statement.create_driver_race_history_table,
        confluent_flink_statement.llm_textgen_model,
      ] :
      strcontains(s.statement, " IF NOT EXISTS ")
    ])
    error_message = "every DDL statement should be IF NOT EXISTS, so a re-apply is a no-op"
  }

  # The file's content is pinned by tests/test_platform_team_sync.py in the
  # workshop repo; here, only that the statement sends it unchanged.
  assert {
    condition = (
      confluent_flink_statement.seed_driver_race_history.statement == file("${path.module}/driver_race_history_seed.sql") &&
      startswith(confluent_flink_statement.seed_driver_race_history.statement, "INSERT INTO `driver_race_history` VALUES\n")
    )
    error_message = "seed_driver_race_history should send driver_race_history_seed.sql as is: an INSERT INTO `driver_race_history` with no catalog"
  }

  assert {
    condition     = confluent_flink_statement.llm_textgen_model.statement == "CREATE MODEL IF NOT EXISTS `John-Doe-c21e_env`.`RIVER-RACING-DEMO-CLUSTER`.`llm_textgen_model` INPUT (prompt STRING) OUTPUT (response STRING) WITH ('provider' = 'confluent', 'task' = 'text_generation', 'confluent.model' = 'anthropic.claude-sonnet-4-6', 'confluent.params.max_tokens' = '50000');"
    error_message = "llm_textgen_model should be the CSP Hosted model, in the environment's catalog and the cluster's database"
  }
}

run "creates_no_keys_service_accounts_tags_or_connections" {
  command = plan

  module {
    source = "./modules/developer"
  }

  assert {
    condition = alltrue([
      for f in fileset(path.module, "*.tf") :
      length(regexall("resource\\s+\"confluent_(service_account|api_key|tag|tag_binding|flink_connection)\"", file("${path.module}/${f}"))) == 0
    ])
    error_message = "modules/developer must not create service accounts, API keys, the RAW_DATA tag or a Bedrock connection."
  }
}

# Q25: statements keep the provider's auto-generated names. With a fixed name,
# the orphan of a double POST would block every re-apply with a 409.
run "statements_keep_auto_generated_names" {
  command = plan

  module {
    source = "./modules/developer"
  }

  assert {
    condition = alltrue([
      for f in fileset(path.module, "*.tf") :
      length(regexall("(?m)^\\s*statement_name\\s*=", file("${path.module}/${f}"))) == 0
    ])
    error_message = "No confluent_flink_statement in modules/developer may set statement_name (Q25): an orphan from the provider's double POST would then block the re-apply with a 409."
  }
}

# A targeted plan includes only the target's dependencies, so each run below
# fails with "Reference to uninitialized resource" unless that statement waits
# for time_sleep.cluster_settle, directly or through another statement. Each
# prints a "Resource targeting is in effect" warning; that's expected.
run "car_telemetry_waits_for_cluster" {
  command = plan

  module {
    source = "./modules/developer"
  }

  plan_options {
    target = [confluent_flink_statement.create_car_telemetry_table]
  }

  assert {
    condition     = time_sleep.cluster_settle.create_duration == "30s"
    error_message = "create_car_telemetry_table should wait for time_sleep.cluster_settle"
  }
}

run "race_standings_waits_for_cluster" {
  command = plan

  module {
    source = "./modules/developer"
  }

  plan_options {
    target = [confluent_flink_statement.create_race_standings_table]
  }

  assert {
    condition     = time_sleep.cluster_settle.create_duration == "30s"
    error_message = "create_race_standings_table should wait for time_sleep.cluster_settle"
  }
}

run "driver_race_history_waits_for_cluster" {
  command = plan

  module {
    source = "./modules/developer"
  }

  plan_options {
    target = [confluent_flink_statement.create_driver_race_history_table]
  }

  assert {
    condition     = time_sleep.cluster_settle.create_duration == "30s"
    error_message = "create_driver_race_history_table should wait for time_sleep.cluster_settle"
  }
}

run "llm_textgen_model_waits_for_cluster" {
  command = plan

  module {
    source = "./modules/developer"
  }

  plan_options {
    target = [confluent_flink_statement.llm_textgen_model]
  }

  assert {
    condition     = time_sleep.cluster_settle.create_duration == "30s"
    error_message = "llm_textgen_model should wait for time_sleep.cluster_settle"
  }
}

# The seed must follow its table, and reaches the settle through it, so this
# run checks both.
run "seed_waits_for_driver_race_history_table" {
  command = plan

  module {
    source = "./modules/developer"
  }

  plan_options {
    target = [confluent_flink_statement.seed_driver_race_history]
  }

  assert {
    condition     = strcontains(confluent_flink_statement.create_driver_race_history_table.statement, "CREATE TABLE IF NOT EXISTS `driver_race_history`")
    error_message = "seed_driver_race_history should depend on create_driver_race_history_table"
  }

  assert {
    condition     = time_sleep.cluster_settle.create_duration == "30s"
    error_message = "seed_driver_race_history should wait for time_sleep.cluster_settle"
  }
}
