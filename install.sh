#!/bin/sh
set -eu

managed_start="# >>> hwskill >>>"
managed_end="# <<< hwskill <<<"

script_path=$0
case "$script_path" in
  */*) ;;
  *) script_path=$(command -v "$script_path" 2>/dev/null || printf '%s' "$script_path") ;;
esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$script_path")" 2>/dev/null && pwd -P || true)

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

configure_shell() {
  repo_root=$1
  bin_dir=$2
  config_path=${HWSKILL_SHELL_CONFIG:-}
  if [ -z "$config_path" ]; then
    case "${SHELL##*/}" in
      zsh) config_path="$HOME/.zshrc" ;;
      bash) config_path="$HOME/.bashrc" ;;
      *) config_path="$HOME/.profile" ;;
    esac
  fi
  mkdir -p "$(dirname -- "$config_path")"
  temp_path=$(mktemp "${config_path}.hwskill.XXXXXX")
  if [ -f "$config_path" ]; then
    awk -v start="$managed_start" -v end="$managed_end" '
      $0 == start { managed = 1; next }
      $0 == end { managed = 0; next }
      !managed { print }
    ' "$config_path" >"$temp_path"
  fi
  repo_quoted=$(shell_quote "$repo_root")
  bin_quoted=$(shell_quote "$bin_dir")
  {
    if [ -s "$temp_path" ]; then printf '\n'; fi
    printf '%s\n' "$managed_start"
    printf 'export HWSKILL_HOME=%s\n' "$repo_quoted"
    printf 'case ":$PATH:" in\n'
    printf '  *:%s:*) ;;\n' "$bin_quoted"
    printf '  *) export PATH=%s:"$PATH" ;;\n' "$bin_quoted"
    printf 'esac\n'
    printf '%s\n' "$managed_end"
  } >>"$temp_path"
  mv "$temp_path" "$config_path"
  printf '%s' "$config_path"
}

install_checkout() {
  repo_root=$1
  python_command=${PYTHON:-python3}
  venv="$repo_root/.venv"
  bin_dir=${HWSKILL_BIN_DIR:-"$HOME/.local/bin"}
  command_path="$bin_dir/hwskill"
  command_target="$repo_root/scripts/hwskill"

  if [ ! -x "$venv/bin/python" ]; then
    "$python_command" -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --disable-pip-version-check -e "$repo_root"

  mkdir -p "$bin_dir"
  if [ -L "$command_path" ]; then
    if [ "$(readlink "$command_path")" != "$command_target" ]; then
      echo "existing symlink is not owned by this checkout: $command_path" >&2
      exit 2
    fi
  elif [ -e "$command_path" ]; then
    echo "existing command is not owned by hwskill: $command_path" >&2
    exit 2
  else
    ln -s "$command_target" "$command_path"
  fi

  shell_config=$(configure_shell "$repo_root" "$bin_dir")
  echo "hwskill installed from $repo_root"
  echo "command: $command_path"
  echo "restart the terminal or run: . $shell_config"
}

if [ -n "$script_dir" ] \
  && [ -f "$script_dir/pyproject.toml" ] \
  && [ -f "$script_dir/scripts/hwskill" ]; then
  install_checkout "$script_dir"
  exit 0
fi

install_home=${HWSKILL_HOME:-"$HOME/.local/share/hwskill"}
repository_url=${HWSKILL_REPOSITORY_URL:-"https://gitcode.com/linkeo2012/hwskills.git"}
if [ ! -e "$install_home" ]; then
  git clone "$repository_url" "$install_home"
elif [ ! -d "$install_home/.git" ]; then
  echo "install target exists but is not a Git checkout: $install_home" >&2
  exit 2
fi

exec "$install_home/install.sh"
