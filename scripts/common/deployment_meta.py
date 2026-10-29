"""Per-track deployment identity: where the prefix comes from, and where it's kept.

Two problems this exists to fix.

**Collision.** The prefix used to be prompted with `e.g. demo or your initials`
and defaulted to whatever was in the root `credentials.env`. On a fresh checkout
that default is empty, so the example nudged everyone toward the same value; and
after any run it inherited whatever the *other* track last wrote. Two live
environments both called `RIVER-RACING-PROD-ENV` is the observed result. Worse,
`f1-workshop-simulator` (the ECR repo) is account-global, so a second person
running `uv run deploy` gets a hard `RepositoryAlreadyExistsException`.

**Teardown.** One root `credentials.env` supplied *both* tracks' Terraform inputs,
so `destroy` rebuilt self-service's TF vars from whatever prefix happened to be in
that file. Deploy standalone after a self-service run and the self-service
teardown targeted the wrong names.

The fix is deterministic identity-derived defaults plus **per-track** metadata:

- The prefix is derived from `$USER` (or a hash of the Confluent owner email when
  `$USER` is generic), so two people get different names automatically and the
  *same* person gets the same name on every rerun — `race`, `reset`, `destroy`
  and screen-shares all stay stable and readable. Not random: a random suffix
  would change on every run and make every one of those commands a lookup.
- Each track's resolved inputs live in `runs/<track>/deployment.env`, so the two
  tracks in one checkout cannot read or clobber each other's values.

`F1_CARD` in the root `credentials.env` stays what it was: the *active card*
selector for the attendee tools. This file is about deployment inputs, not auth.

Note `runs/<track>/deployment.env` deliberately sits *beside* `runs/<track>/
credentials/`, not inside it: `resolve_card()` globs `runs/*/credentials/*.env`,
so a metadata file one level up can never be mistaken for a credential card.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# Confluent display names and ECR repository names both have to stay short and
# boring; 12 alphanumeric characters is what the deploy prompt has always
# enforced interactively (the automated path enforced nothing, which is bug 13).
MAX_PREFIX_LEN = 12

# The identity-derived part, before the track suffix. 8 leaves room for the
# suffix inside MAX_PREFIX_LEN.
BASE_PREFIX_LEN = 8

# Below ~10s/lap the anomaly function cannot accumulate its 12 windows before the
# lap-24 anomaly, so the anomaly never fires and the demo has no payoff. Holds for
# either LAB 3 backend — ML_DETECT_ANOMALIES' `minTrainingSize` and
# AI_DETECT_ANOMALIES' `minContextSize` are both 12. Mirrors MIN_SECONDS_PER_LAP
# in deploy.py.
MIN_SECONDS_PER_LAP = 10

# Shared-account logins that identify a machine, not a person. Hitting one of
# these means falling back to the owner email, otherwise every user on a shared
# box would derive the same prefix and collide exactly as before.
#
# Matched against the *sanitized* login, so entries are written without
# punctuation: `ec2-user` sanitizes to `ec2user`, and an entry spelled with the
# dash would never match — which would hand every user of a shared EC2 box the
# same prefix.
GENERIC_USERS = {
    "",
    "admin",
    "administrator",
    "ec2user",
    "root",
    "runner",
    "ubuntu",
    "user",
    "vagrant",
}


@dataclass(frozen=True)
class Track:
    """One deployment track: its runs/ directory, Terraform tier, and suffix."""

    name: str  # runs/<name>/
    tier: str  # terraform/<tier>/
    suffix: str  # appended to the base prefix to keep tracks distinct
    label: str


STANDALONE = Track("standalone", "aws", "", "standalone demo (Confluent + Postgres/CDC/ECS)")
SELFSERVICE = Track("selfservice", "self-service", "s", "self-service (Confluent-only)")
# Self-service inside a customer's own org, in an environment their platform team
# created and filled. It runs no Terraform: its deployment is the keys recorded in
# runs/pre-provisioned/deployment.env. Its prefix is fixed (`DEMO`, matching the
# platform team's RIVER-RACING-DEMO-* names), so the suffix only keeps a derived
# prefix distinct should anything ever derive one.
PREPROVISIONED = Track(
    "pre-provisioned", "pre-provisioned", "p", "pre-provisioned (Confluent-only, in your platform team's environment)"
)

TRACKS: dict[str, Track] = {t.name: t for t in (STANDALONE, SELFSERVICE, PREPROVISIONED)}


# --- prefix derivation ------------------------------------------------------


def sanitize_prefix(raw: str, limit: int = BASE_PREFIX_LEN) -> str:
    """Lowercase, strip to alphanumerics, truncate. '' if nothing survives."""
    return re.sub(r"[^a-z0-9]", "", (raw or "").lower())[:limit]


def derive_base_prefix(owner_email: str = "") -> str:
    """A deterministic, human-readable identifier for whoever is deploying.

    `$USER` first because it is what a person recognises in
    `RIVER-RACING-kevin-ENV`. When `$USER` is a shared/generic account it
    identifies the machine rather than the person, so fall back to a short hash
    of the Confluent owner email — still deterministic, still unique per person,
    just less pretty. Leading 'u' keeps the result starting with a letter, which
    ECR repository names and DNS-ish labels are happier with.
    """
    user = sanitize_prefix(os.environ.get("USER") or os.environ.get("LOGNAME") or "")
    if user and user not in GENERIC_USERS:
        return user

    if owner_email:
        digest = hashlib.sha256(owner_email.strip().lower().encode()).hexdigest()
        return f"u{digest[: BASE_PREFIX_LEN - 1]}"

    # Nothing to go on. Caller must prompt; returning '' makes that explicit
    # rather than inventing a value that silently collides.
    return ""


def track_prefix(base: str, track: Track) -> str:
    """Apply the track suffix, keeping the result within MAX_PREFIX_LEN."""
    if not base:
        return ""
    room = MAX_PREFIX_LEN - len(track.suffix)
    return f"{base[:room]}{track.suffix}"


def validate_prefix(prefix: str) -> str | None:
    """None when usable, else the reason it isn't. Call before any cloud work."""
    if not prefix:
        return "Prefix is empty."
    if not prefix.isalnum():
        return f"Prefix {prefix!r} must be alphanumeric (letters and digits only)."
    if not prefix.isascii():
        return f"Prefix {prefix!r} must be ASCII."
    if len(prefix) > MAX_PREFIX_LEN:
        return f"Prefix {prefix!r} is {len(prefix)} chars; maximum is {MAX_PREFIX_LEN}."
    return None


def validate_seconds_per_lap(raw: str | int | None) -> tuple[int | None, str | None]:
    """(value, error). Guards the automated path, which used to call int() raw."""
    if raw is None or str(raw).strip() == "":
        return None, "Seconds per lap is not set."
    text = str(raw).strip()
    if not text.isdigit():
        return None, f"Seconds per lap {text!r} is not a whole number."
    value = int(text)
    if value < MIN_SECONDS_PER_LAP:
        return None, (
            f"Seconds per lap {value} is below the {MIN_SECONDS_PER_LAP}s minimum — "
            "anomaly detection can't accumulate its 12 windows before the lap-24 anomaly."
        )
    return value, None


# --- per-track metadata ----------------------------------------------------

KEY_BASE_PREFIX = "F1_BASE_PREFIX"
KEY_RESOLVED_PREFIX = "F1_RESOLVED_PREFIX"
KEY_CARD = "F1_CARD_PATH"
KEY_SECONDS_PER_LAP = "F1_SECONDS_PER_LAP"
KEY_TRACK = "F1_TRACK"
KEY_REGION = "F1_REGION"
# Pre-provisioned only: the environment the deploy resolved (and confirmed) and
# the developer's user ID. Saved before any key exists in it: a re-run is locked
# to that environment, and a teardown must not have to re-query the API for it.
KEY_ENVIRONMENT_ID = "F1_ENVIRONMENT_ID"
KEY_ENVIRONMENT_NAME = "F1_ENVIRONMENT_NAME"
KEY_USER_ID = "F1_USER_ID"
# Pre-provisioned only: the IDs of the three keys the deploy minted for the
# developer — the whole of what it creates. Each is saved the moment its key
# exists, so a run that dies partway still leaves a record to clean up from.
# IDs only; the secrets are on the card.
KEY_GLOBAL_KEY_ID = "F1_GLOBAL_KEY_ID"
KEY_KAFKA_KEY_ID = "F1_KAFKA_KEY_ID"
KEY_SR_KEY_ID = "F1_SR_KEY_ID"
# In deletion order: the Global key last, because it authenticates the deletes.
KEY_ID_FIELDS = (KEY_KAFKA_KEY_ID, KEY_SR_KEY_ID, KEY_GLOBAL_KEY_ID)


def meta_path(root: Path, track: Track) -> Path:
    return root / "runs" / track.name / "deployment.env"


def load_meta(root: Path, track: Track) -> dict[str, str]:
    path = meta_path(root, track)
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def save_meta(root: Path, track: Track, **fields: str) -> Path:
    """Merge `fields` into this track's metadata. Values are plain, not secrets.

    Deliberately holds no credentials: it records which prefix/pacing/card a
    track resolved to, so re-runs and teardown agree with the deployment that
    actually exists. Secrets stay in credentials.env and the card.
    """
    path = meta_path(root, track)
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = load_meta(root, track)
    merged[KEY_TRACK] = track.name
    merged.update({k: str(v) for k, v in fields.items() if v is not None and str(v) != ""})

    lines = [
        f"# {track.label}",
        "# Resolved deployment inputs for this track. Not secrets — see credentials.env.",
        "# Written by `uv run deploy --track <track>`; read by re-runs and destroy.",
        *(f"{k}={v}" for k, v in sorted(merged.items())),
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def drop_meta(root: Path, track: Track, *keys: str) -> None:
    """Remove `keys` from this track's metadata, keeping everything else.

    `save_meta` ignores empty values, so this is the only way to forget one: a key
    ID once its key is deleted, say.
    """
    merged = load_meta(root, track)
    if not merged or not any(key in merged for key in keys):
        return
    clear_meta(root, track)
    save_meta(root, track, **{k: v for k, v in merged.items() if k not in keys})


def recorded_key_ids(root: Path, track: Track) -> dict[str, str]:
    """{field: key ID} for every key this track's metadata records, in deletion order."""
    saved = load_meta(root, track)
    return {field: saved[field] for field in KEY_ID_FIELDS if saved.get(field)}


def clear_meta(root: Path, track: Track) -> None:
    """Drop a track's metadata after a *successful* destroy."""
    meta_path(root, track).unlink(missing_ok=True)


# Written by `deploy --track self-service` once driver_race_history is populated, so a re-run
# doesn't re-seed. It has to be removed by *either* teardown path: it used to
# survive `uv run destroy`, and the next `deploy --track self-service` then printed "already
# seeded" over an empty table — LAB 2's COUNT(*) returns 0 and LAB 4's history
# join returns nothing, with no error anywhere.
SEED_MARKER = ".seeded"


def seed_marker_path(root: Path, track: Track) -> Path:
    return root / "runs" / track.name / SEED_MARKER


def retire_track(root: Path, track: Track) -> list[str]:
    """Clean up everything a torn-down track leaves behind. Returns what it removed.

    Call **only after a successful destroy** — removing these after a failure
    would hide a deployment that still has live resources.

    Clearing the `F1_CARD` pointer alone is not enough. The card *files* stayed
    on disk, so with both tracks used on one machine, tearing one down left two
    cards and no pointer: `resolve_card()` then hard-exits **every** attendee
    tool with "Multiple credential cards found" while exactly one live
    environment exists. Deleting the dead track's cards restores the
    single-candidate case that resolution depends on.
    """
    from scripts.common.credentials import clear_active_card

    run_root = root / "runs" / track.name
    removed: list[str] = []

    # Scoped, so tearing down one track leaves the other's pointer alone.
    clear_active_card(root, only_if_under=run_root)

    creds_dir = run_root / "credentials"
    if creds_dir.is_dir():
        for card in sorted(list(creds_dir.glob("*.env")) + list(creds_dir.glob("*.md"))):
            card.unlink(missing_ok=True)
            removed.append(str(card.relative_to(root)))

    marker = seed_marker_path(root, track)
    if marker.exists():
        marker.unlink(missing_ok=True)
        removed.append(str(marker.relative_to(root)))

    meta = meta_path(root, track)
    if meta.exists():
        clear_meta(root, track)
        removed.append(str(meta.relative_to(root)))

    return removed


# --- reconciling against real state ---------------------------------------


def tf_state_path(root: Path, track: Track) -> Path:
    return root / "terraform" / track.tier / "terraform.tfstate"


def has_state(root: Path, track: Track) -> bool:
    return tf_state_path(root, track).exists()


def is_deployed(root: Path, track: Track) -> bool:
    """Whether this track has a deployment to re-run or tear down.

    Terraform state for the tracks that have a tier; for the pre-provisioned
    track, which runs no Terraform, a key ID recorded in its metadata.
    """
    return has_state(root, track) or bool(recorded_key_ids(root, track))


def prefix_from_state(root: Path, track: Track) -> str | None:
    """The prefix the deployed environment actually uses, read from state.

    `terraform/aws` exposes a `prefix` output directly. `terraform/self-service`
    does not, so recover it from `environment_name`, which is composed as
    `RIVER-RACING-${var.prefix}-ENV` (terraform/self-service/main.tf:16,23).
    The pre-provisioned track has no state (its prefix is fixed and its
    environment is the platform team's), so it always reads as None here.
    Returns None when there is no state or nothing recognisable in it — callers
    treat that as "can't verify", not as "matches".
    """
    state = tf_state_path(root, track)
    if not state.exists():
        return None

    from scripts.common.terraform import run_terraform_output

    try:
        out = run_terraform_output(state)
    except Exception:
        return None

    direct = out.get("prefix")
    if direct:
        return str(direct)

    env_name = str(out.get("environment_name") or "")
    match = re.fullmatch(r"RIVER-RACING-(.+)-ENV", env_name)
    return match.group(1) if match else None


def resolve_prefix(
    root: Path,
    track: Track,
    owner_email: str = "",
    explicit: str | None = None,
) -> tuple[str, str, str | None]:
    """Decide this track's prefix. Returns (prefix, source, error).

    Precedence, first hit wins:
      1. an existing deployment's state — never rename live resources
      2. this track's saved metadata — a rerun reuses what it used before
      3. `explicit` (a flag or an answered prompt)
      4. derived from identity

    `error` is set when `explicit` contradicts what is already deployed; the
    caller must refuse rather than proceed, because applying a different prefix
    over live state orphans every resource the old name created.
    """
    deployed = prefix_from_state(root, track)
    if deployed:
        if explicit and explicit != deployed:
            return (
                deployed,
                "state",
                f"This track is already deployed as {deployed!r}; refusing to switch to {explicit!r}. "
                f"Tear it down with `uv run destroy` first, or re-run without the override.",
            )
        return deployed, "state", None

    saved = load_meta(root, track).get(KEY_RESOLVED_PREFIX)
    if saved and not explicit:
        return saved, "saved", None

    if explicit:
        return explicit, "explicit", None

    base = derive_base_prefix(owner_email)
    return track_prefix(base, track), "derived", None
