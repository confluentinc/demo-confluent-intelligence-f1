"""
Terraform variables file (terraform.tfvars) management utilities.
"""

import shutil
from pathlib import Path


def write_tfvars_file(tfvars_path: Path, content: str) -> bool:
    """
    Write terraform.tfvars file with backup of existing file.

    Args:
        tfvars_path: Path to terraform.tfvars file
        content: Content to write

    Returns:
        True if successful, False otherwise
    """
    try:
        if tfvars_path.exists():
            backup_path = tfvars_path.with_suffix(".tfvars.backup")
            shutil.copy2(tfvars_path, backup_path)

        tfvars_path.parent.mkdir(parents=True, exist_ok=True)

        with open(tfvars_path, "w") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"Error writing {tfvars_path}: {e}")
        return False


def generate_core_tfvars_content(
    api_key: str,
    api_secret: str,
    owner_email: str,
    deployment_id: str = "",
    aws_bedrock_access_key: str = "",
    aws_bedrock_secret_key: str = "",
    aws_session_token: str = "",
) -> str:
    """
    Generate terraform.tfvars content for the core module.

    Region and demo name are handled internally by Terraform (hardcoded region,
    auto-generated random suffix for resource naming).

    Args:
        api_key: Confluent Cloud API key
        api_secret: Confluent Cloud API secret
        owner_email: Owner email for resource tagging
        aws_bedrock_access_key: AWS Bedrock access key for Flink AI connections
        aws_bedrock_secret_key: AWS Bedrock secret key for Flink AI connections
        aws_session_token: AWS session token (for temporary ASIA* credentials)

    Returns:
        Formatted terraform.tfvars content
    """
    content = (
        f'confluent_cloud_api_key    = "{api_key}"\n'
        f'confluent_cloud_api_secret = "{api_secret}"\n'
        f'owner_email                = "{owner_email}"\n'
        f'deployment_id              = "{deployment_id}"\n'
        f'aws_bedrock_access_key     = "{aws_bedrock_access_key}"\n'
        f'aws_bedrock_secret_key     = "{aws_bedrock_secret_key}"\n'
    )
    if aws_session_token:
        content += f'aws_session_token          = "{aws_session_token}"\n'
    return content


def write_tfvars_for_deployment(root: Path, creds: dict[str, str]) -> None:
    """
    Write terraform.tfvars for core. Demo module needs no tfvars
    (everything comes from core via terraform_remote_state).

    Args:
        root: Project root directory
        creds: Credentials dictionary (TF_VAR_* prefixed keys)
    """
    api_key = creds.get("TF_VAR_confluent_cloud_api_key", "")
    api_secret = creds.get("TF_VAR_confluent_cloud_api_secret", "")
    owner_email = creds.get("TF_VAR_owner_email", "")
    deployment_id = creds.get("TF_VAR_deployment_id", "")
    aws_bedrock_access_key = creds.get("TF_VAR_aws_bedrock_access_key", "")
    aws_bedrock_secret_key = creds.get("TF_VAR_aws_bedrock_secret_key", "")
    aws_session_token = creds.get("TF_VAR_aws_session_token", "")

    core_path = root / "terraform" / "core" / "terraform.tfvars"
    content = generate_core_tfvars_content(
        api_key, api_secret, owner_email, deployment_id,
        aws_bedrock_access_key, aws_bedrock_secret_key, aws_session_token,
    )
    if write_tfvars_file(core_path, content):
        print(f"  Wrote {core_path.relative_to(root)}")
