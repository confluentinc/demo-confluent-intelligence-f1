# Local self-service and backup workshop path

Use this path for a solo Confluent-only run or when pre-provisioned attendee
environments can't be made usable before a workshop. Each attendee clones the
repository, provisions one Confluent Cloud environment, and runs the race
simulator on their own computer.
The setup creates Confluent resources only. It doesn't create EC2, ECS, ECR, a
VPC, Postgres, or a CDC connector, and it doesn't need Docker.

This is also the canonical guide for a solo Confluent-only run. The workshop
owner must confirm the credential plan before sending attendees to
this guide. Every attendee needs permission to create resources in a Confluent
Cloud organization and credentials that can invoke the workshop's AWS Bedrock
model. Don't send one shared administrator secret to the room.

## What changes in backup mode

| Normal workshop | Local self-service |
|---|---|
| Instructor provides an isolated environment | Attendee provisions an environment |
| Race simulator runs on ECS | `uv run f1-race` runs in a terminal |
| Postgres CDC loads race history | A bounded Flink insert loads the same 198 rows |
| Instructor starts and resets the fleet | Attendee stops, resets, and restarts their own race |

The attendee SQL and expected race outcome stay the same. The local simulator
defaults to 20 seconds per lap, so one race takes about 20 minutes.

## Required accounts and credentials

Prepare these before installing software:

- A Confluent Cloud account in an organization where you may create an
  environment, Kafka cluster, Flink compute pool, API keys, connections, and
  models.
- A Confluent Cloud API key and secret with those permissions. The setup can
  create a key after a CLI login, or you can paste an existing pair.
- An AWS access key and secret allowed to call `bedrock:InvokeModel` and
  `bedrock:InvokeModelWithResponseStream` in `us-east-1`. Temporary AWS
  credentials also need their session token.
- IBM watsonx Orchestrate access and the workshop race-feed URL if you plan to
  complete Lab 5.

The Confluent and AWS credentials are written to the ignored
`credentials.env` file on your machine. Never paste them into chat, commit them,
or put them in a slide.

## 1. Install the command-line tools

You need Git, `uv`, Terraform 1.3 or later, and the Confluent CLI. The Confluent
CLI is optional only when you already have a suitable Confluent Cloud API key.

### macOS

With Homebrew:

```bash
brew install git uv
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install --cask confluent-cli
```

If you don't use Homebrew, install `uv` with its signed standalone installer and
download Terraform, Git, and the Confluent CLI from their official install pages.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal after the installer finishes.

### Ubuntu or Debian Linux

Install Git and the packages needed by the vendor repositories:

```bash
sudo apt update
sudo apt install -y git curl gnupg wget lsb-release
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install Terraform from HashiCorp's APT repository:

```bash
wget -O - https://apt.releases.hashicorp.com/gpg | \
  sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo \"${UBUNTU_CODENAME:-$VERSION_CODENAME}\") main" | \
  sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install -y terraform
```

Install the Confluent CLI from Confluent's APT repository:

```bash
sudo mkdir -p /etc/apt/keyrings
curl https://packages.confluent.io/confluent-cli/deb/archive.key | \
  sudo gpg --dearmor -o /etc/apt/keyrings/confluent-cli.gpg
sudo chmod go+r /etc/apt/keyrings/confluent-cli.gpg
echo "deb [signed-by=/etc/apt/keyrings/confluent-cli.gpg] https://packages.confluent.io/confluent-cli/deb stable main" | \
  sudo tee /etc/apt/sources.list.d/confluent-cli.list >/dev/null
sudo apt update
sudo apt install -y confluent-cli
```

Open a new terminal so `uv` is on `PATH`.

### Windows 11

1. Install Git from <https://git-scm.com/install/windows>.
2. Install `uv` in PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. Download the Windows AMD64 Terraform ZIP from
   <https://developer.hashicorp.com/terraform/install>, extract
   `terraform.exe`, and add its directory to your user `PATH`.
4. Download the Windows Confluent CLI archive from
   <https://docs.confluent.io/confluent-cli/current/install.html>, extract
   `confluent.exe`, and add its directory to your user `PATH`.
5. Close PowerShell and open a new window.

### Check the installation

```bash
git --version
uv --version
terraform version
confluent version
```

If `uv` reports that Python is missing, let it install Python 3.12:

```bash
uv python install 3.12
```

## 2. Clone and install the repository

```bash
git clone https://github.com/confluentinc/demo-confluent-intelligence-f1.git
cd demo-confluent-intelligence-f1
uv venv
```

Activate the virtual environment:

```bash
# macOS or Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install exactly the dependencies recorded in `uv.lock`:

```bash
uv sync
```

## 3. Sign in to Confluent Cloud if setup will create your API key

Skip this section if the workshop owner gave you a Confluent Cloud API key and
secret.

```bash
confluent login
confluent organization describe
```

For an SSO account, follow the browser login. If automatic browser login isn't
available, use:

```bash
confluent login --no-browser
```

The setup command asks whether to generate a new Cloud API key. Answer `y` only
after the CLI login succeeds. Otherwise answer `n` and paste the existing key
and secret supplied by the workshop owner.

## 4. Provision your environment

From the repository root:

```bash
uv run selfservice up
```

The command asks for:

- Confluent Cloud API key and secret
- Your email address
- A short resource prefix; accept the unique suggestion unless your instructor
  assigned one
- AWS Bedrock access key and secret
- AWS session token when the access key starts with `ASIA`

Choose the default path that leaves Labs 3 and 4 unbuilt. The command creates
your environment, writes a credential card under
`runs/selfservice/credentials/`, and inserts 198 historical race rows. A cold
Flink pool can make the first seed check time out. If setup says the environment
exists but seeding is incomplete, run the same command once more.

For a solo demo where the lab objects should be built automatically, use:

```bash
uv run selfservice up --with-labs
```

Verify the generated card and tables:

```bash
uv run f1-sql
```

```sql
SHOW TABLES;
SELECT COUNT(*) FROM driver_race_history;
```

The count should reach 198. Exit the SQL shell after the check.

## 5. Run Labs 1 and 2 with a local race

Start the simulator in its own terminal:

```bash
uv run f1-race
```

It uses the pacing saved during provisioning (20 seconds per lap by default).
For a slower race or a single non-looping run:

```bash
uv run f1-race --seconds-per-lap 60
uv run f1-race --once
```

Open another terminal in the repository and start the Pit Wall:

```bash
uv run f1-pitwall
```

Sign in to Confluent Cloud with your own account. Open
`RIVER-RACING-<your-prefix>-ENV`, open the Flink SQL workspace, and set the
database to `RIVER-RACING-<your-prefix>-CLUSTER`.

Use the attendee [README.md](../../README.md) for the lab steps, with these backup
mode substitutions:

- Skip Lab 1's credential-claim steps. `selfservice up` already created the
  local card, and you sign in with your own Confluent Cloud account.
- In Lab 2, `driver_race_history` came from a bounded Flink insert rather than a
  Postgres CDC connector. The table still has the same columns and 198 rows.
- The simulator is your `uv run f1-race` terminal, not an instructor service.

## 6. Synchronize the anomaly run

After Lab 2, stop `uv run f1-race` with Ctrl-C. Leave the Pit Wall running, then
clear the old source data:

```bash
uv run reset
```

In the browser SQL workspace, run Lab 3's full `CREATE TABLE car_state`
statement from the attendee [README.md](../../README.md). Wait until it shows
**Running**, then restart the simulator:

```bash
uv run f1-race
```

Continue through Labs 3, 4, and 6. Lab 5 remains optional because it needs IBM
watsonx Orchestrate and a public race-feed service.

### Optional Lab 5

Run the race-feed service against the generated credential card:

```bash
uv run f1-social-feed --creds runs/selfservice/credentials/<prefix>.env
```

Expose port 8080 through an approved HTTPS tunnel, set that public URL in
`servers[0].url` in the root `f1-race-feed-openapi.json`, upload the JSON file to
watsonx Orchestrate, and follow
[Lab 5 in the attendee `README.md`](../../README.md#lab-5-social-media-agent-ibm-watsonx-orchestrate).

### Optional MCP access

To connect a supported coding client to this environment with the generated
credential card:

```bash
uv run setup-mcp
uv run setup-mcp --client codex
uv run setup-mcp --client both
```

## 7. Stop or reset your local workshop

Stop the race with Ctrl-C. To repeat the stream-processing labs:

```bash
uv run reset
```

Run Lab 3 again, wait for it to reach **Running**, and then restart
`uv run f1-race`.

## 8. Tear down

Tear down as soon as the session ends so the Confluent resources stop accruing
cost:

```bash
uv run selfservice down
```

Confirm the command reports a successful destroy. It removes the generated
credential card and deployment metadata but leaves `credentials.env` in place.
Delete or securely archive that local file according to your organization's
credential policy, and revoke any short-lived Confluent or AWS keys issued for
the workshop.

## Recovery checks

### `selfservice up` can't authenticate to Confluent

Check that you supplied a Cloud API key, not a Kafka cluster key. Confirm that
the key's principal may create the resources listed at the start of this guide.

### Terraform reports that a name already exists

Don't choose a new prefix while the first deployment still exists. Run
`uv run selfservice down`, confirm the destroy succeeds, then provision again.

### `car_state` stays empty

Confirm Lab 3 was running before you restarted `f1-race`. The standings table
starts at the latest offset, so earlier standings versions aren't available to
the temporal join. The first `car_state` row appears after its 30-second window
closes; the anomaly flag needs 12 windows of context before it can fire.

### The Bedrock model fails

Confirm the AWS key can invoke Bedrock in `us-east-1`. If you use temporary
credentials, check that `TF_VAR_aws_session_token` is present in
`credentials.env` and hasn't expired.

## Navigation

- **Workshop:** [Attendee walkthrough (README.md)](../../README.md)
- **Backup:** [Local self-service guide](#local-self-service-and-backup-workshop-path)
