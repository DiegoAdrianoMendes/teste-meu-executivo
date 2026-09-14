#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/docs/oss-upstreams.lock.json"
DEST="$ROOT/.vendor/upstreams"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_lock() {
  jq -e '
    def safe_path:
      type == "string"
      and length > 0
      and test("^[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*$")
      and (split("/") | all(. != "." and . != ".."));
    .schemaVersion == 2
    and (.sources | type == "array" and length > 0)
    and (([.sources[].name] | length) == ([.sources[].name] | unique | length))
    and all(.sources[];
      (.name | type == "string" and test("^[a-z0-9][a-z0-9-]*$"))
      and (.repository | type == "string" and startswith("https://github.com/") and endswith(".git"))
      and (.refType == "tag")
      and (.ref | type == "string" and length > 0)
      and (.refObject | type == "string" and test("^[0-9a-f]{40}$"))
      and (.refObjectType == "tag" or .refObjectType == "commit")
      and (.commit | type == "string" and test("^[0-9a-f]{40}$"))
      and (.checkout.mode == "full" or .checkout.mode == "sparse-excluding-proprietary")
      and (.checkout.excludePaths | type == "array")
      and all(.checkout.excludePaths[]; safe_path)
      and (
        (.checkout.mode == "full" and (.checkout.excludePaths | length) == 0)
        or (.checkout.mode == "sparse-excluding-proprietary" and (.checkout.excludePaths | length) > 0)
      )
      and (.submodules.policy == "do-not-initialize")
      and (.submodules.expectedGitlinks | type == "array")
      and all(.submodules.expectedGitlinks[]; safe_path)
      and (.license.evidence | type == "array" and length > 0)
      and all(.license.evidence[];
        (.path | safe_path)
        and (.gitBlob | type == "string" and test("^[0-9a-f]{40}$"))
      )
      and (.sentinelFiles | type == "array" and length > 0)
      and all(.sentinelFiles[]; safe_path)
    )
  ' "$LOCK" >/dev/null || die "invalid upstream lock schema: $LOCK"
}

worktree_is_empty() {
  [[ -z "$(find "$1" -mindepth 1 -maxdepth 1 ! -name .git -print -quit)" ]]
}

guard_local_changes() {
  local checkout="$1"
  local dirty

  dirty="$(git -C "$checkout" status --porcelain=v1 --untracked-files=all)"
  if [[ -n "$dirty" ]] && ! worktree_is_empty "$checkout"; then
    die "refusing to overwrite local changes in $checkout"
  fi
}

ensure_repository() {
  local name="$1"
  local repository="$2"
  local checkout="$3"
  local actual_origin

  if [[ -e "$checkout" ]] && [[ ! -d "$checkout/.git" ]]; then
    die "$checkout exists but is not a Git worktree"
  fi

  if [[ ! -d "$checkout/.git" ]]; then
    mkdir -p "$checkout"
    git -C "$checkout" init --quiet
    git -C "$checkout" remote add origin "$repository"
  fi

  actual_origin="$(git -C "$checkout" remote get-url origin 2>/dev/null || true)"
  [[ "$actual_origin" == "$repository" ]] || {
    die "$name origin mismatch: expected $repository, got ${actual_origin:-<missing>}"
  }

  guard_local_changes "$checkout"
  git -C "$checkout" config remote.origin.promisor true
  git -C "$checkout" config remote.origin.partialclonefilter blob:none
}

configure_checkout() {
  local source="$1"
  local checkout="$2"
  local git_dir
  local excluded_count

  git_dir="$(git -C "$checkout" rev-parse --absolute-git-dir)"
  excluded_count="$(jq -r '.checkout.excludePaths | length' <<<"$source")"

  if [[ "$excluded_count" -eq 0 ]]; then
    git -C "$checkout" config core.sparseCheckout false
    git -C "$checkout" config --unset core.sparseCheckoutCone 2>/dev/null || true
    rm -f "$git_dir/info/sparse-checkout"
    return
  fi

  mkdir -p "$git_dir/info"
  git -C "$checkout" config core.sparseCheckout true
  git -C "$checkout" config core.sparseCheckoutCone false
  {
    printf '/*\n'
    jq -r '.checkout.excludePaths[] | "!/" + . + "/"' <<<"$source"
  } >"$git_dir/info/sparse-checkout"
}

enforce_exclusions() {
  local source="$1"
  local checkout="$2"
  local name excluded tracked

  name="$(jq -r '.name' <<<"$source")"
  while IFS= read -r excluded; do
    [[ -n "$excluded" ]] || continue
    while IFS= read -r -d '' tracked; do
      git -C "$checkout" update-index --skip-worktree -- "$tracked"
    done < <(git -C "$checkout" ls-files -z -- "$excluded")

    if [[ -L "$checkout/$excluded" ]]; then
      die "$name excluded path unexpectedly materialized as a symlink: $excluded"
    fi
    if [[ -d "$checkout/$excluded" ]]; then
      [[ -z "$(find "$checkout/$excluded" -mindepth 1 -print -quit)" ]] || {
        die "$name excluded path contains material and will not be removed: $excluded"
      }
      rmdir "$checkout/$excluded"
    elif [[ -e "$checkout/$excluded" ]]; then
      die "$name excluded path unexpectedly materialized: $excluded"
    fi
  done < <(jq -r '.checkout.excludePaths[]' <<<"$source")
}

fetch_source() {
  local source="$1"
  local name repository ref expected_ref_object expected_ref_type expected_commit
  local checkout actual_ref_object actual_ref_type actual_commit

  name="$(jq -r '.name' <<<"$source")"
  repository="$(jq -r '.repository' <<<"$source")"
  ref="$(jq -r '.ref' <<<"$source")"
  expected_ref_object="$(jq -r '.refObject' <<<"$source")"
  expected_ref_type="$(jq -r '.refObjectType' <<<"$source")"
  expected_commit="$(jq -r '.commit' <<<"$source")"
  checkout="$DEST/$name"

  git check-ref-format "refs/tags/$ref" >/dev/null 2>&1 || die "$name has an invalid tag ref: $ref"

  ensure_repository "$name" "$repository" "$checkout"

  git -C "$checkout" -c protocol.version=2 fetch \
    --quiet \
    --force \
    --no-tags \
    --depth=1 \
    --filter=blob:none \
    origin \
    "refs/tags/$ref:refs/tags/$ref"

  actual_ref_object="$(git -C "$checkout" rev-parse "refs/tags/$ref")"
  actual_ref_type="$(git -C "$checkout" cat-file -t "refs/tags/$ref")"
  actual_commit="$(git -C "$checkout" rev-parse "refs/tags/$ref^{commit}")"

  [[ "$actual_ref_object" == "$expected_ref_object" ]] || {
    die "$name ref object mismatch: expected $expected_ref_object, got $actual_ref_object"
  }
  [[ "$actual_ref_type" == "$expected_ref_type" ]] || {
    die "$name ref type mismatch: expected $expected_ref_type, got $actual_ref_type"
  }
  [[ "$actual_commit" == "$expected_commit" ]] || {
    die "$name commit mismatch: expected $expected_commit, got $actual_commit"
  }

  configure_checkout "$source" "$checkout"
  git -C "$checkout" checkout --quiet --force --detach "$expected_commit"
  enforce_exclusions "$source" "$checkout"
  git -C "$checkout" update-ref refs/heads/klm-pinned "$expected_commit"

  printf '%-24s %s (%s, %s)\n' \
    "$name" "$expected_commit" "$ref" "$(jq -r '.checkout.mode' <<<"$source")"
}

require_command git
require_command jq
require_command find
require_command rmdir
[[ -f "$LOCK" ]] || die "upstream lock not found: $LOCK"
validate_lock
mkdir -p "$DEST"

while IFS= read -r source; do
  fetch_source "$source"
done < <(jq -c '.sources[]' "$LOCK")

"$ROOT/scripts/verify_klm_upstreams.sh"
