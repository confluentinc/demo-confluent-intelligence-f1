output "environment_id" {
  value = confluent_environment.main.id
}

output "environment_name" {
  value = confluent_environment.main.display_name
}

# A test can't reach resources inside a module, so the root's tests read the
# statements' catalog and principal from here. The name is the provider's
# auto-generated one, as listed in the Console.
output "flink_statements" {
  value = {
    for key, s in {
      car_telemetry            = confluent_flink_statement.create_car_telemetry_table
      race_standings           = confluent_flink_statement.create_race_standings_table
      driver_race_history      = confluent_flink_statement.create_driver_race_history_table
      driver_race_history_seed = confluent_flink_statement.seed_driver_race_history
      llm_textgen_model        = confluent_flink_statement.llm_textgen_model
    } :
    key => {
      name      = s.statement_name
      catalog   = s.properties["sql.current-catalog"]
      database  = s.properties["sql.current-database"]
      principal = s.principal[0].id
    }
  }
}
