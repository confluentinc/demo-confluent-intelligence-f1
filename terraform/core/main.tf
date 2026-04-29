provider "confluent" {
  cloud_api_key    = var.confluent_cloud_api_key
  cloud_api_secret = var.confluent_cloud_api_secret
}

data "confluent_organization" "main" {}

locals {
  region      = "us-east-1"
  name_prefix = "RIVER-RACING-${var.deployment_id}"
}

# --- Modules ---

module "environment" {
  source           = "../modules/environment"
  environment_name = "${local.name_prefix}-ENV"
  owner_email      = var.owner_email
}

module "cluster" {
  source         = "../modules/cluster"
  environment_id = module.environment.environment_id
  cluster_name   = "${local.name_prefix}-CLUSTER"
  name_prefix    = local.name_prefix
  cloud_provider = "AWS"
  cloud_region   = local.region
  owner_email    = var.owner_email
}

module "flink" {
  source             = "../modules/flink"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "${local.name_prefix}-ENV"
  cluster_name       = "${local.name_prefix}-CLUSTER"
  name_prefix        = local.name_prefix
  cloud_provider     = "AWS"
  cloud_region       = local.region
  service_account_id = module.cluster.service_account_id
  owner_email        = var.owner_email
}

# --- LLM Connections (AWS Bedrock) ---

resource "confluent_flink_connection" "bedrock_textgen_connection" {
  organization { id = data.confluent_organization.main.id }
  environment  { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal    { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  display_name      = "llm-textgen-connection"
  type              = "BEDROCK"
  endpoint          = "https://bedrock-runtime.${local.region}.amazonaws.com/model/us.anthropic.claude-sonnet-4-5-20250929-v1:0/invoke"
  aws_access_key    = var.aws_bedrock_access_key
  aws_secret_key    = var.aws_bedrock_secret_key
  aws_session_token = var.aws_session_token != "" ? var.aws_session_token : null

  depends_on = [module.cluster]
}

resource "confluent_flink_connection" "bedrock_embedding_connection" {
  organization { id = data.confluent_organization.main.id }
  environment  { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal    { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  display_name      = "llm-embedding-connection"
  type              = "BEDROCK"
  endpoint          = "https://bedrock-runtime.${local.region}.amazonaws.com/model/amazon.titan-embed-text-v1/invoke"
  aws_access_key    = var.aws_bedrock_access_key
  aws_secret_key    = var.aws_bedrock_secret_key
  aws_session_token = var.aws_session_token != "" ? var.aws_session_token : null

  depends_on = [module.cluster]
}

# --- LLM Models (Flink CREATE MODEL statements) ---

resource "confluent_flink_statement" "llm_textgen_model" {
  organization { id = data.confluent_organization.main.id }
  environment  { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal    { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  statement = "CREATE MODEL `${local.name_prefix}-ENV`.`${local.name_prefix}-CLUSTER`.`llm_textgen_model` INPUT (prompt STRING) OUTPUT (response STRING) WITH ('provider' = 'bedrock', 'task' = 'text_generation', 'bedrock.connection' = '${confluent_flink_connection.bedrock_textgen_connection.display_name}', 'bedrock.params.max_tokens' = '50000');"

  properties = {
    "sql.current-catalog"  = "${local.name_prefix}-ENV"
    "sql.current-database" = "${local.name_prefix}-CLUSTER"
  }

  depends_on = [confluent_flink_connection.bedrock_textgen_connection]
}

resource "confluent_flink_statement" "llm_embedding_model" {
  organization { id = data.confluent_organization.main.id }
  environment  { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal    { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  statement = "CREATE MODEL `${local.name_prefix}-ENV`.`${local.name_prefix}-CLUSTER`.`llm_embedding_model` INPUT (text STRING) OUTPUT (embedding ARRAY<FLOAT>) WITH ('provider' = 'bedrock', 'task' = 'embedding', 'bedrock.connection' = '${confluent_flink_connection.bedrock_embedding_connection.display_name}');"

  properties = {
    "sql.current-catalog"  = "${local.name_prefix}-ENV"
    "sql.current-database" = "${local.name_prefix}-CLUSTER"
  }

  depends_on = [confluent_flink_connection.bedrock_embedding_connection]
}
