#!/usr/bin/env bash
# Requires a Developer Certificate of Origin sign-off on every commit in a range:
# a Signed-off-by trailer that names the commit's author and email.
#
#     check-dco.sh BASE HEAD
#
# Without a usable BASE (a new branch, or a force push that dropped it), only HEAD
# is checked. Merge commits carry no sign-off requirement.
set -euo pipefail

base="${1:-}"
head="${2:-HEAD}"
zero=0000000000000000000000000000000000000000

if [[ -z "$base" || "$base" == "$zero" ]] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    commits="$(git rev-list --no-merges -n 1 "$head")"
else
    commits="$(git rev-list --no-merges "$base..$head")"
fi

failed=0
for commit in $commits; do
    author="$(git log -1 --format='%an <%ae>' "$commit")"
    if ! git log -1 --format=%B "$commit" | git interpret-trailers --parse |
        grep -Fixq "Signed-off-by: $author"; then
        echo "$(git log -1 --format='%h %s' "$commit"): no 'Signed-off-by: $author' trailer" >&2
        failed=1
    fi
done

if ((failed)); then
    echo "Sign every commit with 'git commit -s' (see the Developer Certificate of Origin)." >&2
    exit 1
fi
