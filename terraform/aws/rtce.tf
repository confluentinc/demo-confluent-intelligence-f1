# Global key shared by RTCE MCP and Lightning Queries.
resource "confluent_api_key" "rtce" {
  count        = var.enable_rtce ? 1 : 0
  display_name = "${local.name_prefix}-rtce-key"
  description  = "Global API key for RTCE and Lightning Queries"

  owner {
    id          = module.cluster.service_account_id
    api_version = module.cluster.service_account_api_version
    kind        = "ServiceAccount"
  }

  managed_resource {
    id          = "global"
    api_version = "global/v1"
    kind        = "Global"
  }

  depends_on = [module.cluster]
}

output "rtce_api_key" {
  value     = try(confluent_api_key.rtce[0].id, "")
  sensitive = true
}

output "rtce_api_secret" {
  value     = try(confluent_api_key.rtce[0].secret, "")
  sensitive = true
}
