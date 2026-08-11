# =============================================================================
# LLM connections + models (AWS Bedrock) for Flink AI.
#
# Creates the two Bedrock connections (text generation + embedding) and the two
# Flink CREATE MODEL statements (`llm_textgen_model`, `llm_embedding_model`) that
# the labs reference. Bedrock credentials are shared across environments; this
# module is consumed by both terraform/aws (per-attendee) and
# terraform/self-service (solo).
# =============================================================================

terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

resource "confluent_flink_connection" "bedrock_textgen_connection" {
  organization { id = var.organization_id }
  environment { id = var.environment_id }
  compute_pool { id = var.compute_pool_id }
  principal { id = var.service_account_id }
  rest_endpoint = var.flink_rest_endpoint
  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  display_name      = "llm-textgen-connection"
  type              = "BEDROCK"
  endpoint          = "https://bedrock-runtime.${var.region}.amazonaws.com/model/us.anthropic.claude-sonnet-4-5-20250929-v1:0/invoke"
  aws_access_key    = var.aws_bedrock_access_key
  aws_secret_key    = var.aws_bedrock_secret_key
  aws_session_token = var.aws_session_token != "" ? var.aws_session_token : null
}

resource "confluent_flink_connection" "bedrock_embedding_connection" {
  organization { id = var.organization_id }
  environment { id = var.environment_id }
  compute_pool { id = var.compute_pool_id }
  principal { id = var.service_account_id }
  rest_endpoint = var.flink_rest_endpoint
  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  display_name      = "llm-embedding-connection"
  type              = "BEDROCK"
  endpoint          = "https://bedrock-runtime.${var.region}.amazonaws.com/model/amazon.titan-embed-text-v1/invoke"
  aws_access_key    = var.aws_bedrock_access_key
  aws_secret_key    = var.aws_bedrock_secret_key
  aws_session_token = var.aws_session_token != "" ? var.aws_session_token : null
}

resource "confluent_flink_statement" "llm_textgen_model" {
  organization { id = var.organization_id }
  environment { id = var.environment_id }
  compute_pool { id = var.compute_pool_id }
  principal { id = var.service_account_id }
  rest_endpoint = var.flink_rest_endpoint
  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  statement = "CREATE MODEL `${var.environment_name}`.`${var.cluster_name}`.`llm_textgen_model` INPUT (prompt STRING) OUTPUT (response STRING) WITH ('provider' = 'bedrock', 'task' = 'text_generation', 'bedrock.connection' = '${confluent_flink_connection.bedrock_textgen_connection.display_name}', 'bedrock.params.max_tokens' = '50000');"

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_connection.bedrock_textgen_connection]
}

resource "confluent_flink_statement" "llm_embedding_model" {
  organization { id = var.organization_id }
  environment { id = var.environment_id }
  compute_pool { id = var.compute_pool_id }
  principal { id = var.service_account_id }
  rest_endpoint = var.flink_rest_endpoint
  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  statement = "CREATE MODEL `${var.environment_name}`.`${var.cluster_name}`.`llm_embedding_model` INPUT (text STRING) OUTPUT (embedding ARRAY<FLOAT>) WITH ('provider' = 'bedrock', 'task' = 'embedding', 'bedrock.connection' = '${confluent_flink_connection.bedrock_embedding_connection.display_name}');"

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_connection.bedrock_embedding_connection]
}
