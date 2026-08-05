# Workshop prerequisites

Complete these steps before provisioning attendee environments.

**Accept every attendee invitation before running `wsa build`.** The attendee
Terraform reads each Confluent user during `terraform plan`. A pending invitation
causes the plan to fail before any resources are created.

## 1. Organizer access

The organizer needs Confluent Cloud `OrganizationAdmin`, AWS credentials for
`us-east-1` with Bedrock access, Terraform 1.3 or later, Docker, `uv`, the AWS CLI,
and the Confluent CLI. `OrganizationAdmin` is required for invitations and the
Global API keys used by Real-Time Context Engine.

```bash
confluent organization describe
aws sts get-caller-identity
aws bedrock list-foundation-models --region us-east-1 >/dev/null
terraform version
docker info >/dev/null
uv --version
```

## 2. Workshop Setup Accelerator

Clone `confluentinc/workshop-setup-accelerator` next to this repo and build its
`bin/wsa` binary. The wrapper searches `$WSA_HOME`, the sibling checkout, the main
checkout's sibling when running inside a Git worktree, then `$PATH`.

```bash
git clone git@github.com:confluentinc/workshop-setup-accelerator.git ../workshop-setup-accelerator
make -C ../workshop-setup-accelerator build
```

If the checkout lives elsewhere, set `WSA_HOME` to its root.

## 3. Workshop secrets

Copy [credentials.env.example](credentials.env.example) to `credentials.env` and
fill in the five required values, or export them from a secret manager. Values in
the process environment take precedence over `credentials.env`.

```bash
cp credentials.env.example credentials.env
chmod 600 credentials.env
```

`credentials.env` is ignored by Git. Both build and teardown need these values;
Terraform destroy reads the same variables as apply.

## 4. Attendee users and 1Password

Install the 1Password CLI, sign in, and enable the desktop app's CLI integration.
The organizer must own a vault named `Workshop Setup Accelerator Users`. WSA stores
each accepted user's password in item `Account NNN`, field
`confluent-cloud/password`.

```bash
op whoami
op vault get 'Workshop Setup Accelerator Users' >/dev/null
```

Choose an email pattern that routes plus-addressed mail to the organizer, for
example `organizer+f1wp{N}@example.com`. Invite one user for each account number:

```bash
for i in $(seq 1 5); do
  confluent iam user invitation create "organizer+f1wp${i}@example.com"
done
```

Generate and validate the ignored spec with that real pattern. This is the spec
used to accept invitations and prevents WSA from reading the committed placeholder:

```bash
uv run workshop spec-validate \
  --email-pattern 'organizer+f1wp{N}@example.com'
```

Use the same pattern when `create-workshop` prompts for it. Resource prefixes are
shared inside the Confluent organization, so run `confluent environment list`
before starting another workshop.

## 5. Gmail OAuth and invitation acceptance

Create a Google OAuth client of type **Desktop app** for the mailbox that receives
the invitations. Enable the Gmail API and Google Sheets API, then save the client
JSON as `~/.wsa/gmail-credentials.json`. WSA uses a localhost callback on port
8085.

Accept one invitation first, then process the remaining range:

```bash
<wsa>/bin/wsa accept-account-invitation -w .wsa-spec-generated.yaml \
  --accounts 1 --gmail-credentials ~/.wsa/gmail-credentials.json

<wsa>/bin/wsa accept-account-invitation -w .wsa-spec-generated.yaml \
  --accounts 2-5 --gmail-credentials ~/.wsa/gmail-credentials.json
```

Invitation messages are matched within a three-day window. If acceptance fails
after consuming an invitation, issue a new invitation for that address and retry.

```bash
confluent iam user list -o json | grep -c 'organizer+f1wp'
op read 'op://Workshop Setup Accelerator Users/Account 005/confluent-cloud/password' >/dev/null
```

The first count must equal the attendee count. Match the organizer's full alias;
a broad `f1wp` search can count another organizer's users.

## 6. Optional account dispenser

The dispenser needs a Google Form and Sheet plus the Apps Script owned by the WSA
repo. Follow `<wsa>/account-dispenser/SETUP.md`, then put the spreadsheet ID in the
ignored `wsa.env` file:

```bash
WSA_DISPENSER_SPREADSHEET_ID=<spreadsheet-id>
```

Keep the Form link out of this repo. `create-workshop` uploads the final credential
rows after it writes the cards. The workshop works without a dispenser; cards can
be handed out directly.

## Final preflight

```bash
uv run workshop spec-validate \
  --email-pattern 'organizer+f1wp{N}@example.com'
uv run workshop build \
  --accounts 10-11 \
  --account-count 2 \
  --prefix f1reh \
  --email-pattern 'organizer+f1wp{N}@example.com'
```

The example assumes accounts 10 and 11 are accepted, stored in 1Password, and
reserved for testing; substitute your own two non-production account numbers and
an unused prefix. Follow
[WORKSHOP-GUIDE.md](WORKSHOP-GUIDE.md) for validation, race control, reset, and
teardown.
