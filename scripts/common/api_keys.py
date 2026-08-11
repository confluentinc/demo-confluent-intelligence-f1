"""
AWS Bedrock Key Manager — Create and manage AWS IAM credentials for the F1 demo.

Creates a least-privilege IAM user with Bedrock InvokeModel permissions only.
Credentials are saved to credentials.env for use by `uv run deploy`.

Usage:
    uv run api-keys create    # Create AWS IAM user + Bedrock access key
    uv run api-keys destroy   # Revoke access key, optionally delete IAM user

    uv run api-keys create --verbose
    uv run api-keys destroy --keep-user
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

from dotenv import dotenv_values, set_key, unset_key

from .logging_utils import setup_logging
from .terraform import get_project_root
from .ui import prompt_choice, prompt_with_default

# ============================================================================
# CONSTANTS
# ============================================================================

AWS_IAM_USERNAME = "f1-demo-bedrock-user"
AWS_POLICY_NAME = "BedrockInvokeOnly"
AWS_CREDENTIALS_FILE = "API-KEYS-AWS.md"

# ============================================================================
# EXCEPTIONS
# ============================================================================


class MaxKeysReached(Exception):
    """Raised when an IAM user already has 2 access keys (AWS limit)."""


# ============================================================================
# IAM HELPERS
# ============================================================================


def _get_owner_email(project_root: Path) -> str:
    """Read owner email from credentials.env, or prompt if not set."""
    creds_file = project_root / "credentials.env"
    if creds_file.exists():
        creds = dotenv_values(creds_file)
        email = creds.get("TF_VAR_owner_email", "").strip("'\"")
        if email:
            return email
    return prompt_with_default("Owner email (for IAM resource tagging)", default="")


def _get_bedrock_policy() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            }
        ],
    }


def _create_or_get_iam_user(
    iam_client, username: str, owner_email: str, project_root: Path, logger: logging.Logger
) -> None:
    """Create IAM user if it doesn't exist."""
    try:
        iam_client.get_user(UserName=username)
        logger.info(f"IAM user '{username}' already exists")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            tags = [
                {"Key": "Owner", "Value": owner_email},
                {"Key": "Project", "Value": "demo-confluent-intelligence-f1"},
                {"Key": "ManagedBy", "Value": "f1-demo-api-keys"},
            ]
            iam_client.create_user(UserName=username, Tags=tags)
            logger.info(f"Created IAM user '{username}'")
        else:
            raise


def _attach_bedrock_policy(iam_client, username: str, logger: logging.Logger) -> None:
    """Attach inline Bedrock InvokeModel policy to IAM user."""
    iam_client.put_user_policy(
        UserName=username,
        PolicyName=AWS_POLICY_NAME,
        PolicyDocument=json.dumps(_get_bedrock_policy()),
    )
    logger.info(f"Attached policy '{AWS_POLICY_NAME}' to '{username}'")


def _create_access_key(iam_client, username: str, logger: logging.Logger) -> tuple[str, str]:
    """Create a new access key. Raises MaxKeysReached if user already has 2."""
    response = iam_client.list_access_keys(UserName=username)
    existing = response.get("AccessKeyMetadata", [])
    if len(existing) >= 2:
        raise MaxKeysReached(f"User '{username}' already has 2 access keys (AWS limit of 2 per user).")
    response = iam_client.create_access_key(UserName=username)
    key = response["AccessKey"]
    logger.info(f"Created access key {key['AccessKeyId']}")
    return key["AccessKeyId"], key["SecretAccessKey"]


def _save_credentials_file(
    project_root: Path,
    access_key_id: str,
    secret_access_key: str,
    username: str,
    logger: logging.Logger,
) -> None:
    """Write API-KEYS-AWS.md and update credentials.env."""
    content = f"""# F1 Demo — AWS Bedrock Credentials

## AWS Bedrock Access Keys

Use these credentials when running `uv run deploy`:

```
AWS Bedrock Access Key: {access_key_id}
AWS Bedrock Secret Key: {secret_access_key}
```

## Security Notes

- **Do NOT commit these credentials to Git**
- These keys have minimal permissions (Bedrock InvokeModel only)
- Revoke immediately after the demo with: `uv run api-keys destroy`

## Cleanup (After Demo)

```bash
uv run api-keys destroy
```

Or manually via AWS CLI:

```bash
aws iam delete-access-key --user-name {username} --access-key-id {access_key_id}
aws iam delete-user-policy --user-name {username} --policy-name {AWS_POLICY_NAME}
aws iam delete-user --user-name {username}
```

---

**IAM User:** `{username}`
**Policy:** `{AWS_POLICY_NAME}` (inline — bedrock:InvokeModel only)
**Created:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC
"""
    creds_file = project_root / AWS_CREDENTIALS_FILE
    creds_file.write_text(content)
    logger.info(f"Saved credentials to {AWS_CREDENTIALS_FILE}")

    env_file = project_root / "credentials.env"
    if env_file.exists():
        set_key(str(env_file), "TF_VAR_aws_bedrock_access_key", access_key_id)
        set_key(str(env_file), "TF_VAR_aws_bedrock_secret_key", secret_access_key)
        set_key(str(env_file), "TF_VAR_aws_iam_username", username)
        logger.info("Updated credentials.env")


def _cleanup_user_dependencies(iam_client, username: str, logger: logging.Logger) -> bool:
    """Remove all IAM user dependencies before deletion. Returns True on full success."""
    ok = True

    # Groups
    try:
        groups = iam_client.list_groups_for_user(UserName=username).get("Groups", [])
        for g in groups:
            iam_client.remove_user_from_group(UserName=username, GroupName=g["GroupName"])
        logger.info(f"Removed from {len(groups)} group(s)")
    except ClientError as e:
        logger.error(f"Groups cleanup error: {e}")
        ok = False

    # Access keys
    try:
        keys = iam_client.list_access_keys(UserName=username).get("AccessKeyMetadata", [])
        for k in keys:
            iam_client.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
        logger.info(f"Deleted {len(keys)} access key(s)")
    except ClientError as e:
        logger.error(f"Access key cleanup error: {e}")
        ok = False

    # Managed policies
    try:
        policies = iam_client.list_attached_user_policies(UserName=username).get("AttachedPolicies", [])
        for p in policies:
            iam_client.detach_user_policy(UserName=username, PolicyArn=p["PolicyArn"])
        logger.info(f"Detached {len(policies)} managed policy/policies")
    except ClientError as e:
        logger.error(f"Managed policy cleanup error: {e}")
        ok = False

    # Inline policies
    try:
        names = iam_client.list_user_policies(UserName=username).get("PolicyNames", [])
        for name in names:
            iam_client.delete_user_policy(UserName=username, PolicyName=name)
        logger.info(f"Deleted {len(names)} inline policy/policies")
    except ClientError as e:
        logger.error(f"Inline policy cleanup error: {e}")
        ok = False

    # Login profile
    try:
        iam_client.delete_login_profile(UserName=username)
        logger.info("Deleted login profile")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.error(f"Login profile cleanup error: {e}")
            ok = False

    return ok


# ============================================================================
# COMMAND HANDLERS
# ============================================================================


def _create_keys(args: argparse.Namespace, logger: logging.Logger) -> int:
    """Create AWS IAM user + Bedrock access key."""
    if not BOTO3_AVAILABLE:
        print("\nERROR: boto3 is not installed. Run: uv pip install boto3")
        return 1

    project_root = get_project_root()
    owner_email = _get_owner_email(project_root)
    iam_username = prompt_with_default("IAM username", default=AWS_IAM_USERNAME)
    iam_client = boto3.client("iam")

    print("\n" + "=" * 60)
    print("CREATING AWS BEDROCK CREDENTIALS")
    print("=" * 60)

    while True:
        _create_or_get_iam_user(iam_client, iam_username, owner_email, project_root, logger)
        _attach_bedrock_policy(iam_client, iam_username, logger)
        try:
            access_key_id, secret_access_key = _create_access_key(iam_client, iam_username, logger)
            break
        except MaxKeysReached as e:
            print(f"\n{e}")
            choice = prompt_choice(
                "How would you like to proceed?",
                [
                    "Cancel — I'll delete an existing key manually then retry",
                    "Create a new IAM user with a different name",
                ],
            )
            if "Cancel" in choice:
                return 1
            iam_username = prompt_with_default("New IAM username", default=f"{iam_username}-2")

    _save_credentials_file(project_root, access_key_id, secret_access_key, iam_username, logger)

    print("=" * 60)
    print("AWS BEDROCK CREDENTIALS CREATED")
    print("=" * 60)
    print(f"\nCredentials saved to: {AWS_CREDENTIALS_FILE}")
    print("Run 'uv run deploy' to deploy with these credentials.")
    print("\nTo revoke after the demo: uv run api-keys destroy")
    print("=" * 60 + "\n")
    return 0


def _destroy_keys(args: argparse.Namespace, logger: logging.Logger) -> int:
    """Revoke AWS Bedrock access key, optionally delete IAM user."""
    if not BOTO3_AVAILABLE:
        print("\nERROR: boto3 is not installed. Run: uv pip install boto3")
        return 1

    project_root = get_project_root()
    env_file = project_root / "credentials.env"
    env_creds = dotenv_values(str(env_file)) if env_file.exists() else {}
    access_key_id = env_creds.get("TF_VAR_aws_bedrock_access_key")
    iam_username = env_creds.get("TF_VAR_aws_iam_username", AWS_IAM_USERNAME)

    if not access_key_id:
        print("\nNo AWS Bedrock access key found in credentials.env.")
        print("Nothing to destroy.")
        return 1

    iam_client = boto3.client("iam")

    print("\n" + "=" * 60)
    print("DESTROYING AWS BEDROCK CREDENTIALS")
    print("=" * 60)

    try:
        iam_client.delete_access_key(UserName=iam_username, AccessKeyId=access_key_id)
        logger.info(f"Deleted access key {access_key_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            logger.warning(f"Access key {access_key_id} not found (may already be deleted)")
        else:
            raise

    user_deleted = False
    if not args.keep_user:
        print(f"\nDelete IAM user '{iam_username}' entirely?")
        print("  (No = keep the user for future demos)")
        choice = prompt_choice(
            "Delete IAM user?",
            ["No (keep user for future demos)", "Yes (delete user completely)"],
        )
        if "Yes" in choice:
            success = _cleanup_user_dependencies(iam_client, iam_username, logger)
            if success:
                try:
                    iam_client.delete_user(UserName=iam_username)
                    logger.info(f"Deleted IAM user '{iam_username}'")
                    user_deleted = True
                except ClientError as e:
                    if e.response["Error"]["Code"] == "NoSuchEntity":
                        user_deleted = True
                    else:
                        print(f"\nCould not delete user: {e}")
                        print("Please delete manually via AWS Console.")
            else:
                print("\nCould not fully clean up IAM user — please delete manually via AWS Console.")

    # Clear from credentials.env
    if env_file.exists():
        unset_key(str(env_file), "TF_VAR_aws_bedrock_access_key")
        unset_key(str(env_file), "TF_VAR_aws_bedrock_secret_key")
        unset_key(str(env_file), "TF_VAR_aws_iam_username")

    creds_md = project_root / AWS_CREDENTIALS_FILE
    if creds_md.exists():
        creds_md.unlink()
        logger.info(f"Deleted {AWS_CREDENTIALS_FILE}")

    print("=" * 60)
    print("AWS BEDROCK CREDENTIALS DESTROYED")
    print("=" * 60)
    print(f"\n  Access key {access_key_id} deleted")
    if user_deleted:
        print(f"  IAM user '{iam_username}' deleted")
    print("  credentials.env cleared")
    print("=" * 60 + "\n")
    return 0


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


def main() -> int | None:
    """Main entry point for api-keys command."""
    parser = argparse.ArgumentParser(
        description="Create and manage AWS Bedrock credentials for the F1 demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run api-keys create            # Create IAM user + Bedrock access key
  uv run api-keys create --verbose  # Verbose output
  uv run api-keys destroy           # Revoke access key (prompts about IAM user)
  uv run api-keys destroy --keep-user  # Revoke key, keep IAM user
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    create_parser = subparsers.add_parser("create", help="Create AWS Bedrock credentials")
    create_parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    destroy_parser = subparsers.add_parser("destroy", help="Revoke AWS Bedrock credentials")
    destroy_parser.add_argument("--keep-user", action="store_true", help="Keep IAM user for reuse")
    destroy_parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    logger = setup_logging(getattr(args, "verbose", False))

    if args.command == "create":
        return _create_keys(args, logger)
    elif args.command == "destroy":
        return _destroy_keys(args, logger)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
