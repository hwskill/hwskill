#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: filter_minimax_auth.sh INPUT OUTPUT" >&2
  exit 64
fi

input=$1
output=$2
umask 077

if ! test -r "$input"; then
  echo "MiniMax credential source is not readable: $input" >&2
  exit 2
fi

if ! jq -e '
  ."minimax-cn-coding-plan"
  | type == "object"
    and .type == "api"
    and (.key | type == "string" and length > 0)
' "$input" >/dev/null; then
  echo "MiniMax credential missing or malformed" >&2
  exit 2
fi

jq '{"minimax-cn-coding-plan": {"type": ."minimax-cn-coding-plan".type, "key": ."minimax-cn-coding-plan".key}}' "$input" >"$output"
chmod 600 "$output"
