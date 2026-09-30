#!/usr/bin/env bash
# Report how far the declared SREF revision is behind upstream.
#
# A nonblocking maintenance signal; the CI job that runs it may fail.
set -uo pipefail

root="${1:-../sref}"
declared="${SREF_COMMIT:-}"
if [ -z "$declared" ]; then
    echo "SREF_COMMIT is not set, so there is no declared revision to compare" >&2
    exit 2
fi

upstream="$(git -C "$root" rev-parse origin/main)"
echo "declared: $declared"
echo "upstream: $upstream (origin/main)"

if [ "$declared" = "$upstream" ]; then
    echo "the pin is current"
    exit 0
fi

if ! git -C "$root" cat-file -e "${declared}^{commit}" 2>/dev/null; then
    echo "the declared revision is not in this checkout, so nothing can be compared" >&2
    exit 2
fi

echo "upstream is $(git -C "$root" rev-list --count "$declared..$upstream") commit(s) ahead:"
git -C "$root" log --oneline "$declared..$upstream" | sed 's/^/  /'

# What changed matters more than how much. A registry, schema, snapshot or
# conformance change is a reason to look now; everything else can wait.
echo
echo "changes in the parts this implementation is tested against:"
changed="$(git -C "$root" diff --name-only "$declared..$upstream" -- registry schema snapshots conformance spec.md)"
if [ -n "$changed" ]; then
    echo "$changed" | sed 's/^/  /'
    echo
    echo "review these before moving the pin"
else
    echo "  none"
fi
exit 1
