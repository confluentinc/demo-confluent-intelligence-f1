output "mq_public_ip" {
  value = aws_instance.mq.public_ip
}

output "mq_public_dns" {
  value = aws_instance.mq.public_dns
}

output "mq_instance_id" {
  value = aws_instance.mq.id
}

output "mq_connection_string" {
  value = "${aws_instance.mq.public_ip}(1414)"
}
