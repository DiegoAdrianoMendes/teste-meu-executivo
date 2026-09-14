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
      and (
        (has("packageMetadata") | not)
        or (
          (.packageMetadata.path | safe_path)
          and (.packageMetadata.name | type == "string" and length > 0)
          and (.packageMetadata.version | type == "string" and length > 0)
          and (.packageMetadata.license | type == "string" and length > 0)
          and (.packageMetadata.devReact | type == "string" and length > 0)
          and (.packageMetadata.devReactDom | type == "string" and length > 0)
          and (.packageMetadata.peerReact | type == "string" and length > 0)
          and (.packageMetadata.peerReactDom | type == "string" and length > 0)
        )
      )
    )
  ' "$LOCK" >/dev/null || die "invalid upstream lock schema: $LOCK"
}

is_expected_exclusion() {
  local candidate="$1"
  local excluded_paths="$2"
  local excluded

  while IFS= read -r excluded; do
    [[ -n "$excluded" ]] || continue
    case "$candidate" in
      "$excluded" | "$excluded"/*)
        return 0
        ;;
    esac
  done <<<"$excluded_paths"

  return 1
}

verify_ref_and_origin() {
  local source="$1"
  local checkout="$2"
  local name repository ref expected_ref_object expected_ref_type expected_commit
  local actual_origin actual_ref_object actual_ref_type actual_commit actual_head

  name="$(jq -r '.name' <<<"$source")"
  repository="$(jq -r '.repository' <<<"$source")"
  ref="$(jq -r '.ref' <<<"$source")"
  expected_ref_object="$(jq -r '.refObject' <<<"$source")"
  expected_ref_type="$(jq -r '.refObjectType' <<<"$source")"
  expected_commit="$(jq -r '.commit' <<<"$source")"

  git check-ref-format "refs/tags/$ref" >/dev/null 2>&1 || die "$name has an invalid tag ref: $ref"

  [[ -d "$checkout/.git" ]] || die "$name is not materialized as a Git worktree"
  [[ "$(git -C "$checkout" rev-parse --is-inside-work-tree)" == "true" ]] || {
    die "$name is not a valid Git worktree"
  }

  actual_origin="$(git -C "$checkout" remote get-url origin 2>/dev/null || true)"
  [[ "$actual_origin" == "$repository" ]] || {
    die "$name origin mismatch: expected $repository, got ${actual_origin:-<missing>}"
  }
  [[ "$(git -C "$checkout" config --bool --get remote.origin.promisor 2>/dev/null || true)" == "true" ]] || {
    die "$name origin is not configured as a partial-clone promisor"
  }
  [[ "$(git -C "$checkout" config --get remote.origin.partialclonefilter 2>/dev/null || true)" == "blob:none" ]] || {
    die "$name partial-clone filter is not blob:none"
  }

  git -C "$checkout" show-ref --verify --quiet "refs/tags/$ref" || {
    die "$name is missing pinned tag $ref"
  }
  actual_ref_object="$(git -C "$checkout" rev-parse "refs/tags/$ref")"
  actual_ref_type="$(git -C "$checkout" cat-file -t "refs/tags/$ref")"
  actual_commit="$(git -C "$checkout" rev-parse "refs/tags/$ref^{commit}")"
  actual_head="$(git -C "$checkout" rev-parse HEAD)"

  [[ "$actual_ref_object" == "$expected_ref_object" ]] || {
    die "$name ref object mismatch: expected $expected_ref_object, got $actual_ref_object"
  }
  [[ "$actual_ref_type" == "$expected_ref_type" ]] || {
    die "$name ref type mismatch: expected $expected_ref_type, got $actual_ref_type"
  }
  [[ "$actual_commit" == "$expected_commit" ]] || {
    die "$name tag resolves to $actual_commit instead of $expected_commit"
  }
  [[ "$actual_head" == "$expected_commit" ]] || {
    die "$name HEAD mismatch: expected $expected_commit, got $actual_head"
  }
  if git -C "$checkout" symbolic-ref --quiet HEAD >/dev/null; then
    die "$name HEAD must be detached at the pinned commit"
  fi
}

verify_clean_worktree() {
  local name="$1"
  local checkout="$2"
  local dirty extras

  dirty="$(git -C "$checkout" status --porcelain=v1 --untracked-files=all)"
  [[ -z "$dirty" ]] || die "$name worktree has tracked or untracked changes"

  extras="$(git -C "$checkout" clean -ndx)"
  [[ -z "$extras" ]] || die "$name worktree has ignored or untracked material: $extras"
}

verify_checkout_layout() {
  local source="$1"
  local checkout="$2"
  local name excluded_count sparse_enabled git_dir pattern_file expected_patterns actual_patterns
  local line candidate excluded excluded_paths tracked_lines total_count skipped_count materialized_count

  name="$(jq -r '.name' <<<"$source")"
  excluded_count="$(jq -r '.checkout.excludePaths | length' <<<"$source")"
  sparse_enabled="$(git -C "$checkout" config --bool --get core.sparseCheckout 2>/dev/null || printf 'false')"
  excluded_paths="$(jq -r '.checkout.excludePaths[]' <<<"$source")"

  if [[ "$excluded_count" -gt 0 ]]; then
    [[ "$sparse_enabled" == "true" ]] || die "$name proprietary exclusions require sparse checkout"
    git_dir="$(git -C "$checkout" rev-parse --absolute-git-dir)"
    pattern_file="$git_dir/info/sparse-checkout"
    [[ -f "$pattern_file" ]] || die "$name sparse-checkout pattern file is missing"
    expected_patterns="$({
      printf '/*\n'
      jq -r '.checkout.excludePaths[] | "!/" + . + "/"' <<<"$source"
    })"
    actual_patterns="$(sed '/^[[:space:]]*$/d' "$pattern_file")"
    [[ "$actual_patterns" == "$expected_patterns" ]] || die "$name sparse-checkout patterns differ from the lock"
  else
    [[ "$sparse_enabled" == "false" ]] || die "$name must use a full checkout"
  fi

  skipped_count=0
  while IFS= read -r line; do
    [[ "${line:0:2}" == "S " ]] || continue
    candidate="${line:2}"
    skipped_count=$((skipped_count + 1))
    is_expected_exclusion "$candidate" "$excluded_paths" || {
      die "$name unexpectedly skipped tracked path $candidate"
    }
  done < <(git -C "$checkout" ls-files -t)

  while IFS= read -r excluded; do
    [[ -n "$excluded" ]] || continue
    [[ ! -e "$checkout/$excluded" && ! -L "$checkout/$excluded" ]] || {
      die "$name proprietary path was materialized: $excluded"
    }
    tracked_lines="$(git -C "$checkout" ls-files -t -- "$excluded")"
    [[ -n "$tracked_lines" ]] || die "$name locked exclusion is absent from the pinned tree: $excluded"
    while IFS= read -r line; do
      [[ "${line:0:2}" == "S " ]] || die "$name exclusion is not skip-worktree: ${line:2}"
    done <<<"$tracked_lines"
  done <<<"$excluded_paths"

  total_count="$(git -C "$checkout" ls-files | wc -l | tr -d '[:space:]')"
  materialized_count=$((total_count - skipped_count))
  [[ "$materialized_count" -gt 0 ]] || die "$name has no materialized tracked files"
  printf '%s\t%s\t%s\n' "$materialized_count" "$skipped_count" "$total_count"
}

verify_submodules() {
  local source="$1"
  local checkout="$2"
  local name actual_gitlinks expected_gitlinks git_dir gitlink

  name="$(jq -r '.name' <<<"$source")"
  actual_gitlinks="$(git -C "$checkout" ls-files --stage | awk '$1 == "160000" {print $4}' | LC_ALL=C sort)"
  expected_gitlinks="$(jq -r '.submodules.expectedGitlinks[]' <<<"$source" | LC_ALL=C sort)"
  [[ "$actual_gitlinks" == "$expected_gitlinks" ]] || {
    die "$name gitlinks differ from the lock (expected: ${expected_gitlinks:-none}; actual: ${actual_gitlinks:-none})"
  }

  git_dir="$(git -C "$checkout" rev-parse --absolute-git-dir)"
  while IFS= read -r gitlink; do
    [[ -n "$gitlink" ]] || continue
    [[ ! -e "$checkout/$gitlink/.git" && ! -L "$checkout/$gitlink/.git" ]] || {
      die "$name submodule is initialized in the worktree: $gitlink"
    }
    [[ ! -e "$git_dir/modules/$gitlink" && ! -L "$git_dir/modules/$gitlink" ]] || {
      die "$name submodule objects were downloaded: $gitlink"
    }
  done <<<"$actual_gitlinks"
}

verify_excluded_objects() {
  local source="$1"
  local checkout="$2"
  local name downloaded_exclusive_blobs

  name="$(jq -r '.name' <<<"$source")"
  downloaded_exclusive_blobs="$(
    comm -12 \
      <(
        comm -23 \
          <(git -C "$checkout" ls-files -s -t | awk '$1 == "S" && $2 != "160000" {print $3}' | LC_ALL=C sort -u) \
          <(git -C "$checkout" ls-files -s -t | awk '$1 != "S" && $2 != "160000" {print $3}' | LC_ALL=C sort -u)
      ) \
      <(
        git -C "$checkout" cat-file --batch-all-objects --batch-check='%(objectname) %(objecttype)' 2>/dev/null \
          | awk '$2 == "blob" {print $1}' \
          | LC_ALL=C sort -u
      )
  )"

  [[ -z "$downloaded_exclusive_blobs" ]] || {
    die "$name object store contains blobs exclusive to excluded paths: $(tr '\n' ',' <<<"$downloaded_exclusive_blobs" | sed 's/,$//')"
  }
}

verify_sentinels_and_license() {
  local source="$1"
  local checkout="$2"
  local name commit sentinel evidence evidence_path expected_blob tree_blob worktree_blob

  name="$(jq -r '.name' <<<"$source")"
  commit="$(jq -r '.commit' <<<"$source")"

  while IFS= read -r sentinel; do
    [[ -f "$checkout/$sentinel" ]] || die "$name sentinel is not materialized: $sentinel"
    git -C "$checkout" cat-file -e "$commit:$sentinel" || die "$name sentinel is not tracked at the pin: $sentinel"
  done < <(jq -r '.sentinelFiles[]' <<<"$source")

  while IFS= read -r evidence; do
    evidence_path="$(jq -r '.path' <<<"$evidence")"
    expected_blob="$(jq -r '.gitBlob' <<<"$evidence")"
    [[ -f "$checkout/$evidence_path" ]] || die "$name license evidence is not materialized: $evidence_path"
    tree_blob="$(git -C "$checkout" rev-parse "$commit:$evidence_path")"
    worktree_blob="$(git hash-object "$checkout/$evidence_path")"
    [[ "$tree_blob" == "$expected_blob" ]] || {
      die "$name license evidence changed at the pin: $evidence_path"
    }
    [[ "$worktree_blob" == "$expected_blob" ]] || {
      die "$name worktree license evidence differs from the pin: $evidence_path"
    }
  done < <(jq -c '.license.evidence[]' <<<"$source")

  if jq -e '.license.rootLicenseAbsent == true' <<<"$source" >/dev/null; then
    if git -C "$checkout" cat-file -e "$commit:LICENSE" 2>/dev/null; then
      die "$name lock says the root LICENSE is absent, but the pin contains it"
    fi
    [[ ! -e "$checkout/LICENSE" && ! -L "$checkout/LICENSE" ]] || {
      die "$name unexpectedly materialized a root LICENSE"
    }
  fi
}

verify_package_metadata() {
  local source="$1"
  local checkout="$2"
  local path name version license dev_react dev_react_dom peer_react peer_react_dom

  jq -e 'has("packageMetadata")' <<<"$source" >/dev/null || return 0

  path="$(jq -r '.packageMetadata.path' <<<"$source")"
  name="$(jq -r '.packageMetadata.name' <<<"$source")"
  version="$(jq -r '.packageMetadata.version' <<<"$source")"
  license="$(jq -r '.packageMetadata.license' <<<"$source")"
  dev_react="$(jq -r '.packageMetadata.devReact' <<<"$source")"
  dev_react_dom="$(jq -r '.packageMetadata.devReactDom' <<<"$source")"
  peer_react="$(jq -r '.packageMetadata.peerReact' <<<"$source")"
  peer_react_dom="$(jq -r '.packageMetadata.peerReactDom' <<<"$source")"

  jq -e \
    --arg name "$name" \
    --arg version "$version" \
    --arg license "$license" \
    --arg dev_react "$dev_react" \
    --arg dev_react_dom "$dev_react_dom" \
    --arg peer_react "$peer_react" \
    --arg peer_react_dom "$peer_react_dom" \
    '.name == $name
      and .version == $version
      and .license == $license
      and .devDependencies.react == $dev_react
      and .devDependencies["react-dom"] == $dev_react_dom
      and .peerDependencies.react == $peer_react
      and .peerDependencies["react-dom"] == $peer_react_dom' \
    "$checkout/$path" >/dev/null || die "package metadata assertion failed: $path"
}

verify_source() {
  local source="$1"
  local name checkout counts materialized skipped total license_status

  name="$(jq -r '.name' <<<"$source")"
  checkout="$DEST/$name"

  verify_ref_and_origin "$source" "$checkout"
  verify_clean_worktree "$name" "$checkout"
  counts="$(verify_checkout_layout "$source" "$checkout")"
  materialized="$(cut -f1 <<<"$counts")"
  skipped="$(cut -f2 <<<"$counts")"
  total="$(cut -f3 <<<"$counts")"
  verify_submodules "$source" "$checkout"
  verify_excluded_objects "$source" "$checkout"
  verify_sentinels_and_license "$source" "$checkout"
  verify_package_metadata "$source" "$checkout"

  license_status="$(jq -r '.license.status' <<<"$source")"
  printf 'OK  %-21s HEAD=%s files=%s/%s excluded=%s license=%s\n' \
    "$name" "$(jq -r '.commit' <<<"$source")" "$materialized" "$total" "$skipped" "$license_status"
}

require_command git
require_command jq
require_command awk
require_command comm
require_command cut
require_command sed
require_command sort
require_command tr
[[ -f "$LOCK" ]] || die "upstream lock not found: $LOCK"
validate_lock

while IFS= read -r source; do
  verify_source "$source"
done < <(jq -c '.sources[]' "$LOCK")

printf 'All %s OSS upstreams match the lock.\n' "$(jq -r '.sources | length' "$LOCK")"
