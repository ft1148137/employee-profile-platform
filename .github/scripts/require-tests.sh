#!/usr/bin/env bash
set -euo pipefail

filter_result=$1
changed=$2
shift 2
test "$filter_result" = success
case "$changed" in
  true) expected=success ;;
  false) expected=skipped ;;
  *) exit 1 ;;
esac
test "$#" -gt 0
for result in "$@"; do
  test "$result" = "$expected"
done
