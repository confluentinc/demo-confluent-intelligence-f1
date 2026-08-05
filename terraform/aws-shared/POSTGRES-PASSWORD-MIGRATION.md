# Postgres password migration

New shared-infrastructure deployments generate a Postgres password once and keep it stable in Terraform state. The password flows from the Postgres module to the shared outputs, then through the workshop tooling into each attendee CDC connector.

Adding the generated password to an existing shared deployment changes EC2 user data. Cloud-init does not rerun in response to an in-place user-data update, so this migration explicitly replaces and reseeds the Postgres host. This avoids exporting a new password while the running container still has the old one. The instance lifecycle also replaces the host whenever `random_password.postgres` changes in a future password rotation. Replacement changes the public IP, causes downtime, and resets the demo database to the checked-in seed data.

The shared host starts with 105 replication slots: the accelerator's supported maximum of 95 accounts plus ten spare slots. `TF_VAR_attendee_count` remains accepted for workshop-tool compatibility but no longer changes this boot-time configuration, so resuming a run at a different attendee count does not replace or restart Postgres. If a workshop genuinely needs more than 95 accounts, set `TF_VAR_postgres_max_replication_slots` and follow this same explicit-replacement procedure; an in-place update cannot reconfigure the already-running container.

Do not run a full-stack apply for this migration. First preserve a backup of the shared Terraform state, schedule a maintenance window, and inspect targeted plans:

```bash
terraform -chdir=terraform/aws-shared state pull > /tmp/f1-shared-state-before-postgres-password.json
terraform -chdir=terraform/aws-shared plan \
  -target=module.postgres.aws_instance.postgres \
  -target=null_resource.wait_for_postgres \
  -replace=module.postgres.aws_instance.postgres \
  -out=/tmp/f1-postgres-password-migration.tfplan
terraform -chdir=terraform/aws-shared show /tmp/f1-postgres-password-migration.tfplan
```

The plan must show creation of `module.postgres.random_password.postgres` and replacement of `module.postgres.aws_instance.postgres`. Review the security-group change as well: SSH ingress is removed unless `ssh_ingress_cidr` contains an explicitly approved operator CIDR. The managed CDC connector still reaches port 5432 through the existing public ingress rule.

After approval, apply only the saved targeted plan with `terraform -chdir=terraform/aws-shared apply /tmp/f1-postgres-password-migration.tfplan`.

Update each attendee from its existing WSA account state. Pass the new shared host
and password using the same injected variables as the original build, and target
only `confluent_connector.postgres_cdc` in both the saved plan and apply. Do not run
a full attendee-stack apply for this password migration. The exact working
directory and workspace come from that account's WSA run state; using a fresh
directory would create a second connector instead of updating the existing one.

Verify the shared output without printing the password, inspect the connector
configuration through a redacting tool, and confirm every connector reaches
`RUNNING` before ending the maintenance window.

The generated password lives in Terraform state and in EC2 user data, which is available to principals allowed to call `ec2:DescribeInstanceAttribute` and to processes on the instance through IMDS. Store state in an access-controlled backend, restrict those IAM permissions, and do not commit the state backup or saved plan; both contain secrets.
