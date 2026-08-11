output "textgen_connection_name" {
  value = confluent_flink_connection.bedrock_textgen_connection.display_name
}

output "embedding_connection_name" {
  value = confluent_flink_connection.bedrock_embedding_connection.display_name
}
