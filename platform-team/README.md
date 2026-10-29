# Platform team guide: F1 Pit Wall AI Workshop

**This guide is for the team that owns your Confluent Cloud organization.** It is everything you need to prepare, run and clean up the workshop's environments, step by step, in the order you do it. You don't need the rest of the workshop repository; only the instructor's rehearsal uses it.

## What this does

This Terraform module builds **each developer's whole workshop stack**. Per developer, it creates:

- **a new, empty environment**, named after their email (see [Environment names](#environment-names)), with the Stream Governance Advanced package, and a role binding that makes the developer **EnvironmentAdmin** on it through their own user account;
- **a Standard, single-zone Kafka cluster**, `RIVER-RACING-DEMO-CLUSTER`, on AWS in `region` (default `us-east-1`, the same for everyone). The environment's Schema Registry comes up with it;
- **a Flink compute pool**, `RIVER-RACING-DEMO-pool`, that scales on demand up to `flink_max_cfu` CFUs (default 10);
- **the workshop's tables and AI model:** `car_telemetry` and `race_standings` (empty until the developer starts the race), `driver_race_history` with its 198 historical rows, and `llm_textgen_model` on the Confluent-hosted `anthropic.claude-sonnet-4-6` (`csp_hosted_model`).

**It creates no service accounts and no API keys.** Your only key is the OrganizationAdmin Global key you run this module with. It is the provider credential and signs every Flink statement, which runs as the key's owner.

**One environment per person, instructors included,** and no spares: an environment is only useful to the person bound to it, and each one runs a Kafka cluster. Someone who joins late is added in minutes ([step 9](#9-add-everyone-else)).

On the developer side, each person signs in to the Console (through SSO, if you use it), runs `confluent login`, then `uv run deploy --track pre-provisioned` from the [workshop repository](https://github.com/confluentinc/demo-confluent-intelligence-f1), with no Terraform. The deploy mints a Global, a Kafka and a Schema Registry key for their own account and writes them to a local credential card. It creates nothing else in the environment, except the `driver_race_history` rows if the table is empty. The developer's own lab statements run as the developer, in your compute pool. The full developer steps are in the [pre-provisioned walkthrough](../docs/tracks/PRE-PROVISIONED.md).

## Your steps

| When | Step |
|---|---|
| A week or more before | [1. Install the tools and sign in](#1-install-the-tools-and-sign-in) |
| | [2. Get an OrganizationAdmin Global API key](#2-get-an-organizationadmin-global-api-key) |
| | [3. Check that every developer is a user in your organization](#3-check-that-every-developer-is-a-user-in-your-organization) |
| | [4. Check your quotas](#4-check-your-quotas) (raising one takes time) |
| | [5. Check the region](#5-check-the-region) |
| | [6. Check networking and access](#6-check-networking-and-access) |
| A few days before | [7. Create the instructors' environments](#7-create-the-instructors-environments) |
| | [8. Rehearse the developer flow](#8-rehearse-the-developer-flow) |
| Shortly before the workshop | [9. Add everyone else](#9-add-everyone-else) |
| | [10. Send each developer their environment](#10-send-each-developer-their-environment) |
| During the workshop | [When a developer comes to you](#when-a-developer-comes-to-you) |
| Afterwards | [11. Tear down](#11-tear-down) |

The clusters bill from the moment they're created, so apply everyone's stacks shortly before the workshop; the instructors' go first, for the rehearsal.

## 1. Install the tools and sign in

You need:

- **Terraform 1.3 or later** (`terraform version`). `terraform init` downloads `confluentinc/confluent` (pinned to exactly `2.92.0`), `hashicorp/http` (`3.6.3`), `hashicorp/time` (`0.14.2`) and `hashicorp/random` from the Terraform Registry. The optional tests in `tests/` need Terraform 1.7 or later.
- **The Confluent CLI** (`confluent version`), for the checks in the next steps.

Sign in to the CLI as an OrganizationAdmin:

```bash
confluent login
```

Enter your work email. With SSO, it opens your organization's sign-in page in your browser.

## 2. Get an OrganizationAdmin Global API key

The module runs with one Global API key owned by an OrganizationAdmin. It must be a **Global** key, not a Cloud resource management key, because it also signs every Flink statement. The module never creates it.

List the Global keys you already own:

```bash
confluent api-key list --resource global --current-user
```

```text
  Current |       Key        | Description |   Owner   |   Owner Email    | Resource Type | ...
----------+------------------+-------------+-----------+------------------+---------------+
          | ABCDEFGH12345678 |             | u-a1b2c3  | you@example.com  | global        |
```

Reuse one of them if you have its secret. Otherwise create one:

```bash
confluent api-key create --resource global
```

It prints the key and its secret. **Save the secret now: it's shown only once.** A user can own at most 2 Global keys; if you already have 2, delete one you no longer use in the Console under **API keys** → **My account**.

Then confirm that the key's owner (the `Owner` column above, `u-…`) is an OrganizationAdmin:

```bash
confluent iam rbac role-binding list --principal User:<owner-id> --role OrganizationAdmin
```

One row means it is. `None found.` means it isn't: use a key owned by an OrganizationAdmin instead.

A service account with the OrganizationAdmin role can own the key too: `confluent api-key create --resource global --service-account <sa-id>`, then check it with `--principal User:<sa-id>`.

## 3. Check that every developer is a user in your organization

The module looks each email up and **never invites anyone:** an email that isn't a user fails the plan.

```bash
confluent iam user list
```

Every developer's email must be in the `Email` column, and that is how you'll write it in [step 7](#7-create-the-instructors-environments). If someone is missing:

- **With SSO,** a user appears after their first sign-in. Ask them to sign in to [Confluent Cloud](https://confluent.cloud) once.
- **Without SSO,** invite them, and have them accept the invitation from their email:

    ```bash
    confluent iam user invitation create <email>
    confluent iam user invitation list
    ```

    An accepted invitation shows `INVITE_STATUS_ACCEPTED`.

Developers need no roles before the apply: it makes each one EnvironmentAdmin on their own environment.

## 4. Check your quotas

The apply creates one environment and one Kafka cluster per person, all at once. With N people (instructors included), run:

```bash
confluent service-quota list organization --quota-code iam.max_environments.per_org
confluent service-quota list organization --quota-code iam.max_kafka_clusters.per_org
confluent service-quota list organization --quota-code kafka.max_kafka_creation.per_day
```

Each prints one row, like this (columns after `Usage` trimmed):

```text
           Quota Code          |              Name              |    Scope     | Applied Limit | Usage | ...
-------------------------------+--------------------------------+--------------+---------------+-------+
  iam.max_environments.per_org | Max Environments Per           | organization |            50 | 22    |
                               | Organization                   |              |               |       |
```

**For each of the three, `Applied Limit` minus `Usage` must be at least N.** An empty `Usage` means 0. In the example, the organization has room for 28 more environments. Leave some headroom on `kafka.max_kafka_creation.per_day` in case you have to replace a stack on the same day.

Raise a quota through Confluent Support. Allow time for the request.

## 5. Check the region

Everyone's cluster and compute pool go in the one `region` (default `us-east-1`):

```bash
confluent flink region list --cloud aws
confluent rtce region list
```

- **Flink:** the first command must list your region.
- **Real-Time Context Engine:** if the second doesn't list it, developers skip the walkthrough's optional Real-Time Context Engine section and the challenges that use it.
- **The Confluent-hosted model,** `anthropic.claude-sonnet-4-6` (or the `csp_hosted_model` you set), must be offered there. No CLI command shows it: confirm with your Confluent account team. The rehearsal ([step 8](#8-rehearse-the-developer-flow)) confirms it in practice.

## 6. Check networking and access

There are no commands for these; confirm them with your network and identity teams:

- **No IP filter blocks the developers' networks** from Kafka, Flink, Schema Registry or the Confluent Cloud management API.
- **The developers' own network allows outbound traffic to Confluent Cloud** on ports 443 and 9092. The workshop's race simulator runs on each developer's laptop and produces to Kafka directly.
- **Public networking only.** Each developer's cluster is a Standard cluster on public endpoints.
- **Keep the module's direct role bindings.** Each developer must be EnvironmentAdmin directly, as a role binding on their user, which is what the module creates. Don't replace it with an SSO group mapping: a user's API keys don't carry group-mapped permissions, so a developer who is EnvironmentAdmin only through a group can't deploy.
- **New, empty environments only.** The module always creates fresh environments; don't swap in environments you already have. A reused environment can already have a Schema Registry in another region. In our testing, an environment whose Schema Registry was in `eu-central-1` made the first schema registration fail.

## 7. Create the instructors' environments

Run everything from this folder. First, create your settings file:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

- `confluent_cloud_api_key` and `confluent_cloud_api_secret`: the Global key from [step 2](#2-get-an-organizationadmin-global-api-key). To keep them out of the file, delete those two lines and export `TF_VAR_confluent_cloud_api_key` and `TF_VAR_confluent_cloud_api_secret` instead.
- `developer_emails`: for now, only the instructors' emails, as `confluent iam user list` shows them. Everyone else is added in [step 9](#9-add-everyone-else).
- Optionally `region`, `flink_max_cfu` and `csp_hosted_model`; the file shows their defaults.

Then:

```bash
terraform init
terraform plan -out=terraform.tfplan
terraform apply terraform.tfplan
rm terraform.tfplan
```

- **`terraform plan`** ends with `Plan: N to add, 0 to change, 0 to destroy.`, where N is 11 per person: a random name suffix, the environment, the role binding, the cluster, the compute pool, a 30-second pause once the cluster is up, and five Flink statements (three tables, the seed and the model). It fails before creating anything if:
    - your key or secret is wrong, or the key has expired or been deleted;
    - an email isn't a user in your organization;
    - the same person appears twice (emails are compared case-insensitively);
    - an email's part before the `@` has no letter or digit to name an environment after.
- **`terraform apply`** takes a few minutes, mostly waiting for the clusters. If it stops, see [If the apply stops](#if-the-apply-stops).
- **`rm terraform.tfplan`:** the saved plan contains your API secret in plain text.

Then check the result:

```bash
terraform plan
terraform output -raw handout
```

- **`terraform plan`** must report `No changes.` Applying again never renames an environment, because each name's suffix is kept in state.
- **`terraform output -raw handout`** lists each person's environment name and ID (see [step 10](#10-send-each-developer-their-environment)).

To see one environment's Flink statements, with its ID from the handout:

```bash
confluent flink statement list --environment <env-id> --cloud aws --region <region>
```

All five of yours (named `tf-…`) should be `COMPLETED`. The seed is the `INSERT INTO driver_race_history` one: Terraform returns as soon as it starts running, so this is where you see it finish.

### If the apply stops

If it stops on **`Statement with name "tf-…" already exists`**, or on a statement that failed with **"technical difficulties on our end"**, run the `plan -out`, `apply` and `rm` commands again. Both errors are transient. The tables and the model are created with `IF NOT EXISTS`, so a second run only adds what's missing; the seed is the exception (see [The `driver_race_history` seed](#the-driver_race_history-seed)).

## 8. Rehearse the developer flow

Have an instructor run the whole developer flow in their own environment, a few days ahead: the [pre-provisioned walkthrough](../docs/tracks/PRE-PROVISIONED.md)'s deploy, then the walkthrough's core steps through to the pit-strategy agent and its pit decisions. Confirm that:

- `SHOW MODELS;` in the Flink SQL workspace lists `anthropic.claude-sonnet-4-6`;
- the deploy finishes. It counts the `driver_race_history` rows, so a clean finish shows the seed worked;
- the pit-strategy agent (`CREATE AGENT`, then `AI_RUN_AGENT`) runs and writes pit-stop decisions.

The agent calls a Confluent-hosted model. **Confirm with your Confluent account team how that inference is billed** in your organization.

**Lightning Queries:** the walkthrough's optional Challenge #2 uses Lightning Queries, which is Early Access and may not be enabled for your organization. During the rehearsal, have the instructor enable the Real-Time Context Engine on the `car_telemetry` topic in the Console (the walkthrough shows how), then run, from the workshop repository:

```bash
uv run setup-rtce --lightning
```

If it reports `api_not_enabled`, Lightning Queries aren't enabled for your organization: ask your Confluent account team, or tell developers to skip Challenge #2. Otherwise it prints a `curl` command. Run it: a JSON query result rather than an error (HTTP 200) means Lightning Queries work.

## 9. Add everyone else

Shortly before the workshop, add everyone else's emails to `developer_emails`, then run the same commands as in step 7:

```bash
terraform plan -out=terraform.tfplan
terraform apply terraform.tfplan
rm terraform.tfplan
```

**Check the plan before you apply it:** it must say `0 to change, 0 to destroy`, with 11 to add per new person. Each stack belongs to its exact email string, so:

- **removing an email destroys that person's whole stack:** the environment, the cluster, the compute pool, the tables, and any work of theirs in it;
- **changing an existing entry**, even only its case, replaces that stack with a new, empty one.

The same applies to anyone who joins late: add their email, plan and apply. Existing stacks aren't touched (unless it's been more than 30 days, see [The `driver_race_history` seed](#the-driver_race_history-seed)).

Afterwards, `terraform plan` must again report `No changes.`

## 10. Send each developer their environment

```bash
terraform output -raw handout
```

```text
EMAIL                   ENVIRONMENT          ENVIRONMENT ID  CONSOLE URL
jane.doe@example.com    jane-doe-7f3a_env    env-a1b2c3      https://confluent.cloud/environments/env-a1b2c3
john.smith@example.com  john-smith-c21e_env  env-d4e5f6      https://confluent.cloud/environments/env-d4e5f6
```

**Send each developer their own row,** with a link to the [pre-provisioned walkthrough](../docs/tracks/PRE-PROVISIONED.md). They need the environment name and the Console URL. The environment ID is for your records: the deploy finds the environment by itself.

## When a developer comes to you

The deploy stops with a message that names you in these cases:

| The developer sees | What to do |
|---|---|
| `Your platform team hasn't provisioned <name> (<id>) yet: it has no …` | Their stack is incomplete, most likely because an apply stopped. Run the `plan -out`, `apply` and `rm` commands again (see [If the apply stops](#if-the-apply-stops)). |
| `Your Global key sees no environment you're directly bound to.` | Their email is missing from `developer_emails`, or they signed in with another account. Check the [handout](#10-send-each-developer-their-environment) and which email they used. |
| `driver_race_history holds N rows, expected 198` | Rebuild their table: see [The `driver_race_history` seed](#the-driver_race_history-seed). |

A developer who sees **`Confluent Cloud refused a new Global key`** already holds the 2 Global keys a user may own. They delete one in the Console under **API keys** → **My account**; you don't need to do anything.

### The `driver_race_history` seed

The 198 rows are one plain `INSERT`. Terraform returns as soon as it starts running, so **a clean apply doesn't prove the rows landed**, and a re-apply after the errors in [If the apply stops](#if-the-apply-stops) can occasionally insert them twice.

**Each developer's deploy is the check.** It counts the rows, carries on at 198, seeds an empty table itself, and on any other count stops with `driver_race_history holds N rows, expected 198` and sends the developer to you. To rebuild that developer's table, with the Confluent CLI signed in as an OrganizationAdmin and their environment ID from the [handout](#10-send-each-developer-their-environment):

```bash
confluent kafka cluster list --environment <env-id>    # their cluster ID
confluent kafka topic delete driver_race_history --environment <env-id> --cluster <cluster-id>
confluent schema-registry schema delete --subject driver_race_history-value --version all --environment <env-id>
confluent schema-registry schema delete --subject driver_race_history-value --version all --environment <env-id> --permanent
```

If there is also a `driver_race_history-key` subject, delete it the same way. Then recreate the table and its seed, with the developer's email exactly as it appears in `developer_emails`:

```bash
terraform plan -out=terraform.tfplan \
  -replace='module.developer["jane.doe@example.com"].confluent_flink_statement.create_driver_race_history_table' \
  -replace='module.developer["jane.doe@example.com"].confluent_flink_statement.seed_driver_race_history'
terraform apply terraform.tfplan
rm terraform.tfplan
```

Once the new seed statement shows as `COMPLETED` (`confluent flink statement list`, as in [step 7](#7-create-the-instructors-environments)), the developer runs their deploy again.

Two more things about the seed:

- **Don't edit or regenerate `modules/developer/driver_race_history_seed.sql` between apply and destroy.** A changed statement is most likely replaced, which would insert the 198 rows again.
- **Re-applying more than 30 days after the first apply re-runs the seed.** Confluent Cloud deletes finished statements after 30 days, so Terraform creates them again: the tables and the model are unaffected, but each `driver_race_history` gets its rows twice. Rebuild as above.

## 11. Tear down

1. **Each developer runs `uv run destroy`.** It deletes the three API keys their deploy minted, Global key included, and their local credential card. It leaves the environment and everything in it to you.
2. **You run, from this folder:**

    ```bash
    terraform destroy
    ```

    It lists everything it will delete and asks you to type `yes`: the statements, compute pools, clusters, role bindings and environments this module created. `confluent environment list` no longer shows them afterwards.

No extra step is needed when developers' own Flink statements are still running in their pools: in our testing, `terraform destroy` removed the compute pool, cluster and environment with a developer's statement still running. We're re-confirming this with the workshop's full set of lab statements.

If a developer can't run step 1, their keys outlive their stack. The Kafka and Schema Registry keys are useless once the cluster and environment are gone, but the Global key keeps any other roles that developer holds in your organization. Have them delete it in the Console under **API keys** → **My account**, or delete it yourself.

## Reference

### Environment names

Each environment is named after the part of the email before the `@`, plus a random 4-character suffix and `_env`. Characters other than letters, digits, `_` and `-` become `-` (a run of them becomes a single `-`), the result is cut to 40 characters and any `-` or `_` is trimmed from its ends, and the case is kept. For example, `John.Doe@example.com` gets an environment like `John-Doe-c21e_env`.

**Don't rename the environments, or the cluster and compute pool inside them.** A developer who can see more than one environment is matched to theirs by its name, and the deploy finds the cluster and pool by theirs.

### Cost

- **Per developer, from your apply until your destroy:** a Standard Kafka cluster, and Flink compute (CFUs, used on demand, up to `flink_max_cfu` per developer). Developers' own streaming statements keep running, and can keep using compute, until you destroy their stack, even after they've run `uv run destroy`. Plus the pit-strategy agent's AI model calls (see [step 8](#8-rehearse-the-developer-flow)).
- **Per environment, until you destroy it:** Stream Governance Advanced. Every environment is created with the Advanced package, which is billed per environment and **can't be downgraded**.

### Terraform state

State is local by default (`terraform.tfstate` in this folder). To keep it somewhere shared, add your own `backend` block before the first `terraform init`.

**The state holds your Global key's secret in plain text:** in every Flink statement's credentials, and in the request that looks up the key's owner. Protect `terraform.tfstate` as you would the key: keep it out of version control and readable only by you, or use a backend that encrypts state. It also holds your developers' user details (email, user ID, name) and their environments' names and IDs. It holds no developer secrets, because developers mint their own keys.

### Files in this folder

| File | Purpose |
|---|---|
| `versions.tf` | Terraform and provider version pins |
| `providers.tf` | The Confluent provider, configured with your OrganizationAdmin Global key |
| `variables.tf` | Your key and secret, `developer_emails` and the checks on it, `region`, `flink_max_cfu`, `csp_hosted_model` |
| `main.tf` | The user lookups, the lookup of your key's owner, the name suffixes, and one `modules/developer` per email |
| `outputs.tf` | The `handout` table |
| `modules/developer/` | One developer's stack: environment, role binding, cluster, compute pool, and the tables, seed and model (`statements.tf`, `driver_race_history_seed.sql`) |
| `terraform.tfvars.example` | A starting point for your `terraform.tfvars` |
| `tests/` | Offline checks of the naming, the email list and the developer stack (`terraform test`); every provider is mocked, so they never contact Confluent Cloud |
