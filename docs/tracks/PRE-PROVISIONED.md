# Pre-provisioned workshop walkthrough

![F1 Pit Wall Confluent Intelligence architecture](../assets/architecture.png)

> [!NOTE]
>
> Use this path when your organization's platform team has created a Confluent Cloud environment for you, made you its EnvironmentAdmin, and built the workshop inside it. You sign in with your own work account, connect to that environment as yourself (`uv run deploy --track pre-provisioned`), and run the race simulator on your machine. **If you have your own Confluent Cloud account and nobody created an environment for you,** use the [self-service workshop walkthrough](./SELF-SERVICE.md). **If your instructor gave you a workshop login and password,** use the [hosted workshop walkthrough](./HOSTED-WORKSHOP.md).

This page covers setup only. Once your deploy finishes, the workshop continues in the [self-service walkthrough](./SELF-SERVICE.md#1-enhance-the-data-with-built-in-ml-functions-and-start-the-race) from section 1, with one difference (see [Continue in the self-service walkthrough](#continue-in-the-self-service-walkthrough)).

**On the platform team?** The environments, and everything in them, are created with the Terraform module in `platform-team/`; see the [platform team guide](../../platform-team/README.md).

## 0. Sign in, install, and connect

You need:

- **Your environment's name**, from your platform team. It's named after the part of your email before the `@`, with a 4-character suffix and `_env`: for example, `John-Doe-c21e_env`.
- **Your Confluent Cloud sign-in** for your organization, through single sign-on (SSO) if your organization uses it.

There's no payment step and no Terraform: your platform team already built the workshop's Kafka cluster, Flink compute pool, tables and AI model in your environment. The deploy only creates API keys for your own account and a credential card on your machine.

1. Sign in to the [Confluent Cloud Console](https://confluent.cloud) with your work email. With SSO, the Console sends you to your organization's sign-in page.

    Open **Environments** and check that your environment is listed. If it isn't, ask your platform team before you go on.

2. Install the prerequisites, then clone the repo:

    <details>
    <summary><strong>Prerequisites for your machine</strong> — skip this step if they're already installed (click to expand)</summary>

    <details>
    <summary>for Mac</summary>

    ```bash
    brew install git uv
    brew install --cask confluent-cli
    ```

    </details>

    <details>
    <summary>for Windows</summary>

    ```powershell
    winget install --id Git.Git -e
    winget install --id astral-sh.uv -e
    ```

    The Confluent CLI has no reliable winget package. Download the latest `confluent_*_windows_amd64.zip` from the [Confluent CLI releases](https://github.com/confluentinc/cli/releases/latest), unzip it, and add the folder containing `confluent.exe` to your `PATH`. Then close and reopen the terminal so every tool is on your `PATH`.

    </details>

    </details>

    ```bash
    git clone https://github.com/confluentinc/demo-confluent-intelligence-f1.git
    cd demo-confluent-intelligence-f1
    ```

3. Sign in to the Confluent CLI in the same terminal:

    ```bash
    confluent login
    ```

    Enter your work email. With SSO, it opens your organization's sign-in page in your browser. The deploy uses this session to create your Global API key as you.

4. Connect to your environment:

    ```bash
    uv run deploy --track pre-provisioned
    ```

    It creates three API keys for your account: a Global key (with the Confluent CLI), then a Kafka key and a Schema Registry key. It writes them to your credential card, `runs/pre-provisioned/credentials/DEMO.env`, which git ignores. Your Global key acts with all of your permissions, so keep the card private. Along the way it asks:

    | Prompt | What to enter |
    |---|---|
    | `Continue?` | Press **Enter**. The summary above it says the deploy creates your keys and nothing else in your environment, except the `driver_race_history` rows if they're missing |
    | `Your email (the one you sign in to Confluent Cloud with)` | Asked only if Confluent Cloud doesn't return your email: enter your work email |
    | `Deploy into <name> (<id>)?` | Check that `<name>` is the environment your platform team gave you, then press **Enter**. If you can see several environments and none is named after your email, you get a numbered list and `Which one is yours?` instead |
    | `Start the Pit Wall dashboard in the background now?` | Press **Enter** to start the dashboard at http://localhost:8000, or answer `n` to start it yourself later |

    Between prompts it prints what it finds: `User: u-…`, your email, `Environment: <name> (<id>)`, then the cluster, compute pool and Schema Registry your platform team built. New keys take a minute or two to become active, so expect a couple of short waits. Before it finishes, it checks that `driver_race_history` holds its 198 rows.

    When it finishes, it prints a link to your Flink SQL workspace, your catalog and database, and the next steps. Running it again reuses your credential card while its keys still work, so it creates nothing new.

    <details>
    <summary>Troubleshooting the deploy</summary>

    - **`Error: Not logged into Confluent Cloud.`** Your Confluent CLI session is missing or has expired. Run `confluent login`, then the deploy again.
    - **`Confluent Cloud refused a new Global key`.** Each account can hold 2 Global keys. Delete one you no longer use in the Console under **API keys** → **My account**, then run the deploy again.
    - **`Your Global key sees no environment you're directly bound to.`** Ask your platform team for a direct EnvironmentAdmin role binding on your user. A binding through an SSO group mapping isn't enough, because API keys don't carry group-mapped permissions. If you belong to several Confluent Cloud organizations, the deploy lists them: run `confluent login --organization <id>` for the right one, then the deploy again.
    - **`Your platform team hasn't provisioned <name> (<id>) yet`.** Send them the environment ID it names, and run the deploy again once they're done.
    - **`driver_race_history holds N rows, expected 198`.** Ask your platform team to rebuild the table (their guide shows how), then run the deploy again.
    - **A wait that times out, or `could not reach the Confluent Cloud API`.** Run the deploy again. A run that stops before writing your card deletes the keys it created, so retrying never uses up your Global-key slots.

    </details>

## Continue in the self-service walkthrough

Go to [section 1 of the self-service walkthrough](./SELF-SERVICE.md#1-enhance-the-data-with-built-in-ml-functions-and-start-the-race) and follow it from there. It's the same workshop, with one difference to remember:

> [!IMPORTANT]
> **Your catalog is your environment** (for example `John-Doe-c21e_env`), not `RIVER-RACING-DEMO-ENV`. The database is still `RIVER-RACING-DEMO-CLUSTER`.

A few smaller notes for the rest of that walkthrough:

- **Real-Time Context Engine:** the RTCE key on your credential card is the Global key the deploy created for you, so `uv run setup-rtce` doesn't ask for another one.
- **Your credential card** is `runs/pre-provisioned/credentials/DEMO.env`. Where the self-service troubleshooting tips mention `runs/selfservice/...` or `uv run deploy --track self-service`, use this path and `uv run deploy --track pre-provisioned`.
- **`uv run reset`** (in [Run the workshop again or tear it down](./SELF-SERVICE.md#run-the-workshop-again-or-tear-it-down)) uses your Confluent CLI session from step 3. If it has expired, it asks you to sign in; with SSO, press **Enter** at the email prompt and accept the single sign-on login.
- **Tear down** as in [Tear down](#tear-down) below, not the self-service way.

## Tear down

Stop `uv run f1-race` with Ctrl-C when you finish. Then:

```bash
uv run destroy
```

It deletes the three API keys the deploy created for you, Global key last, and your credential card. Your environment and everything in it stay: they belong to your platform team, who delete them afterwards. It lists the keys, and says so, before it asks you to confirm. Afterwards, it asks whether to delete `credentials.env` too (default **yes**).

If it stops partway, run it again: it deletes only what's left. If your credential card is gone, or its Global key no longer works, it lists the keys for you to delete in the Console under **API keys** → **My account**.

---

**← Back to Overview**: [Main README](../../README.md)
