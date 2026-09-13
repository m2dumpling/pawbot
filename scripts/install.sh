#!/bin/sh
set -eu

package="pawbot-ai"
install_target="${PAWBOT_INSTALL_TARGET:-$package}"
install_source="PyPI"
dry_run="0"
pawbot_runner=""
pawbot_python=""
pawbot_launcher=""
install_failure_reason=""

info() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

install_failure_hint() {
  privilege_prefix="sudo "
  if [ "$(id -u 2>/dev/null || printf '1')" = "0" ]; then
    privilege_prefix=""
  fi
  printf '%s\n' "Pawbot installation failed." >&2
  case "$install_failure_reason" in
    venv)
      printf '%s\n' "Reason: Python's venv/ensurepip support is missing for $python_bin." >&2
      printf '%s\n' "Debian/Ubuntu (use the version-specific package first):" >&2
      printf '  %s\n' "${privilege_prefix}apt-get update && ${privilege_prefix}apt-get install -y python$python_version-venv" >&2
      printf '%s\n' "If that package is unavailable, use:" >&2
      printf '  %s\n' "${privilege_prefix}apt-get update && ${privilege_prefix}apt-get install -y python3-venv" >&2
      printf '%s\n' "Fedora/RHEL:" >&2
      printf '  %s\n' "${privilege_prefix}dnf install -y python3-venv python3-pip" >&2
      printf '%s\n' "Arch Linux:" >&2
      printf '  %s\n' "${privilege_prefix}pacman -S --needed python" >&2
      ;;
    pip)
      printf '%s\n' "Reason: pip/ensurepip is unavailable for $python_bin." >&2
      printf '%s\n' "Use uv or pipx, or install the Python packaging tools for your distribution." >&2
      ;;
    cli)
      printf '%s\n' "Reason: the package installation completed, but the pawbot CLI could not be started." >&2
      ;;
    *)
      printf '%s\n' "Reason: the package could not be installed from $install_source." >&2
      printf '%s\n' "If pip reported externally-managed-environment, use uv, pipx, or a virtual environment." >&2
      ;;
  esac
  printf '%s\n' "After fixing the reason above, rerun the Pawbot installer." >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: install.sh [--dry-run]

By default this installs or upgrades pawbot-ai from PyPI.
Use --dry-run to print what would happen without installing or starting setup.

For current main, clone the repository and run `python -m pip install -e .`.
EOF
}

find_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

python_is_virtual_env() {
  "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)
PY
}

ensure_pip() {
  target_python="$1"
  if "$target_python" -m pip --version >/dev/null 2>&1; then
    return 0
  fi

  info "pip was not found for $target_python. Trying ensurepip..."
  "$target_python" -m ensurepip --upgrade >/dev/null 2>&1 || {
    install_failure_reason="pip"
    return 1
  }
}

python_version=""

get_python_version() {
  "$python_bin" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
}

check_venv_support() {
  if "$python_bin" - <<'PY' >/dev/null 2>&1
import venv
import ensurepip
PY
  then
    return 0
  fi

  install_failure_reason="venv"
  return 1
}

run_pawbot() {
  case "$pawbot_runner" in
    uv)
      uv tool run --from "$install_target" pawbot "$@"
      ;;
    pipx)
      pipx run --spec "$install_target" pawbot "$@"
      ;;
    python)
      "$pawbot_python" -m pawbot "$@"
      ;;
    *)
      fail "pawbot was installed, but no runner was configured"
      ;;
  esac
}

pawbot_try_command() {
  if [ -n "$pawbot_launcher" ] && [ -x "$pawbot_launcher" ]; then
    printf '"%s"\n' "$pawbot_launcher"
    return 0
  fi
  case "$pawbot_runner" in
    uv)
      printf '%s\n' "uv tool run --from \"$install_target\" pawbot"
      ;;
    pipx)
      printf '%s\n' "pipx run --spec \"$install_target\" pawbot"
      ;;
    python)
      printf '"%s" -m pawbot\n' "$pawbot_python"
      ;;
  esac
}

show_default_entrypoint() {
  entrypoint="$1"
  if has_browser_session; then
    info "Run: $entrypoint (opens the WebUI)"
  else
    info "Run: $entrypoint agent (opens the native TUI; use 'pawbot webui' explicitly for WebUI)"
  fi
}

default_bin_dir() {
  if [ "$(id -u 2>/dev/null || printf '1')" = "0" ] &&
    [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    printf '%s\n' /usr/local/bin
    return 0
  fi

  old_ifs="$IFS"
  IFS=:
  for path_dir in ${PATH:-}; do
    if [ -n "$path_dir" ] && [ -d "$path_dir" ] && [ -w "$path_dir" ]; then
      IFS="$old_ifs"
      printf '%s\n' "$path_dir"
      return 0
    fi
  done
  IFS="$old_ifs"
  printf '%s\n' "$HOME/.local/bin"
}

is_fresh_pawbot_install() {
  [ -n "${HOME:-}" ] || return 1
  [ ! -e "$HOME/.pawbot/config.json" ]
}

has_browser_session() {
  if [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]; then
    return 1
  fi
  if ! : 2>/dev/null < /dev/tty; then
    return 1
  fi

  case "$(uname -s)" in
    Darwin)
      command -v launchctl >/dev/null 2>&1 &&
        launchctl print "gui/$(id -u)" >/dev/null 2>&1
      ;;
    *)
      [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]
      ;;
  esac
}

install_with_active_python() {
  info "Detected an active virtual environment. Installing into it..."
  ensure_pip "$python_bin" || return 1
  "$python_bin" -m pip install --upgrade "$install_target" || return 1
  pawbot_runner="python"
  pawbot_python="$python_bin"
}

install_with_uv() {
  info "Installing or upgrading pawbot from $install_source with uv tool..."
  uv tool install --python "$python_bin" --force --upgrade "$install_target" || return 1
  pawbot_runner="uv"
  write_pawbot_launcher
}

install_with_pipx() {
  info "Installing or upgrading pawbot from $install_source with pipx..."
  pipx install --python "$python_bin" --force "$install_target" || return 1
  pawbot_runner="pipx"
  write_pawbot_launcher
}

write_pawbot_launcher() {
  [ -n "${HOME:-}" ] || return 0
  bin_dir="${PAWBOT_BIN_DIR:-$(default_bin_dir)}"
  pawbot_launcher="$bin_dir/pawbot"
  mkdir -p "$bin_dir" || return 0

  if [ -e "$pawbot_launcher" ] && ! grep -q "Generated by pawbot installer" "$pawbot_launcher" 2>/dev/null; then
    info "Not updating $pawbot_launcher because it already exists."
    pawbot_launcher=""
    return 0
  fi

  case "$pawbot_runner" in
    uv)
      cat > "$pawbot_launcher" <<EOF
#!/bin/sh
# Generated by pawbot installer.
exec uv tool run --from "$install_target" pawbot "\$@"
EOF
      ;;
    pipx)
      cat > "$pawbot_launcher" <<EOF
#!/bin/sh
# Generated by pawbot installer.
exec pipx run --spec "$install_target" pawbot "\$@"
EOF
      ;;
    python)
      cat > "$pawbot_launcher" <<EOF
#!/bin/sh
# Generated by pawbot installer.
exec "$pawbot_python" -m pawbot "\$@"
EOF
      ;;
    *)
      pawbot_launcher=""
      return 0
      ;;
  esac
  chmod +x "$pawbot_launcher" || return 0

  info "Installed a pawbot launcher at $pawbot_launcher."
}

install_with_managed_venv() {
  [ -n "${HOME:-}" ] || fail "HOME is not set; cannot create a managed virtual environment"

  venv_dir="${PAWBOT_VENV:-$HOME/.pawbot/venv}"
  venv_python="$venv_dir/bin/python"

  if [ ! -x "$venv_python" ]; then
    if ! check_venv_support; then
      return 1
    fi
    info "Creating a dedicated virtual environment at $venv_dir..."
    mkdir -p "$(dirname "$venv_dir")"
    "$python_bin" -m venv "$venv_dir" || return 1
  fi

  "$venv_python" - <<'PY' >/dev/null 2>&1 || fail "The managed venv uses Python older than 3.11. Remove it or set PAWBOT_VENV to a new path."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

  info "Installing or upgrading pawbot from $install_source in $venv_dir..."
  ensure_pip "$venv_python" || return 1
  "$venv_python" -m pip install --upgrade "$install_target" || return 1

  pawbot_runner="python"
  pawbot_python="$venv_python"
  write_pawbot_launcher
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev)
      fail "--dev installed an untracked main snapshot and is no longer supported; clone the repository and run 'python -m pip install -e .' instead"
      ;;
    --dry-run)
      dry_run="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

python_bin="${PYTHON:-}"

if [ -n "$python_bin" ]; then
  command -v "$python_bin" >/dev/null 2>&1 || fail "PYTHON=$python_bin was not found"
  "$python_bin" - <<'PY' >/dev/null 2>&1 || fail "pawbot requires Python 3.11 or newer"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
else
  python_bin="$(find_python)" || fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
fi

python_version="$(get_python_version)"
info "Using Python: $("$python_bin" --version 2>&1)"

if [ "$dry_run" = "1" ]; then
  info "Dry run: would install or upgrade pawbot from $install_source."
  if python_is_virtual_env; then
    info "Dry run: active virtual environment detected; would run: $python_bin -m pip install --upgrade $install_target"
    info "Dry run: would run pawbot as: $python_bin -m pawbot"
  elif command -v uv >/dev/null 2>&1; then
    info "Dry run: would run: uv tool install --python $python_bin --force --upgrade $install_target"
    info "Dry run: would run pawbot as: uv tool run --from $install_target pawbot"
  elif command -v pipx >/dev/null 2>&1; then
    info "Dry run: would run: pipx install --python $python_bin --force $install_target"
    info "Dry run: would run pawbot as: pipx run --spec $install_target pawbot"
  else
    if check_venv_support; then
      venv_dir="${PAWBOT_VENV:-$HOME/.pawbot/venv}"
      info "Dry run: would create or reuse a dedicated virtual environment: $venv_dir"
      info "Dry run: would run: $venv_dir/bin/python -m pip install --upgrade $install_target"
      info "Dry run: would run pawbot as: $venv_dir/bin/python -m pawbot"
    else
      info "Dry run: would stop because Python venv/ensurepip support is missing."
      info "Dry run: install the version-specific python${python_version}-venv package first."
    fi
  fi
  if [ "${PAWBOT_SKIP_WIZARD:-}" = "1" ]; then
    info "Dry run: would skip automatic setup because PAWBOT_SKIP_WIZARD=1."
  elif is_fresh_pawbot_install && has_browser_session; then
    info "Dry run: would start the WebUI for this fresh desktop install."
    info "Dry run: would fall back to the setup wizard for older releases."
  else
    info "Dry run: would run the setup wizard."
  fi
  info "Dry run: no changes made."
  exit 0
fi

if python_is_virtual_env; then
  install_with_active_python || install_failure_hint
else
  installed="0"

  if command -v uv >/dev/null 2>&1; then
    if install_with_uv; then
      installed="1"
    else
      info "uv tool install failed. Trying the next isolated install method..."
    fi
  fi

  if [ "$installed" != "1" ] && command -v pipx >/dev/null 2>&1; then
    if install_with_pipx; then
      installed="1"
    else
      info "pipx install failed. Trying the managed virtual environment..."
    fi
  fi

  if [ "$installed" != "1" ]; then
    info "Using a dedicated virtual environment to avoid system pip."
    install_with_managed_venv || install_failure_hint
  fi
fi

info "Installed pawbot:"
if ! run_pawbot --version; then
  install_failure_reason="cli"
  install_failure_hint
fi

show_install_success() {
  info "Installation successful."
  if [ -n "$pawbot_launcher" ] && [ -x "$pawbot_launcher" ]; then
    resolved_pawbot="$(command -v pawbot 2>/dev/null || true)"
    if [ "$resolved_pawbot" = "$pawbot_launcher" ]; then
      info "CLI verified on PATH: $resolved_pawbot"
      show_default_entrypoint "pawbot"
    else
      bin_dir="$(dirname "$pawbot_launcher")"
      info "CLI verified at: $pawbot_launcher"
      if [ -n "$resolved_pawbot" ]; then
        info "This shell currently resolves pawbot to: $resolved_pawbot"
      else
        info "This shell does not include $bin_dir in PATH."
      fi
      if has_browser_session; then
        info "Run now: \"$pawbot_launcher\" (opens the WebUI)"
      else
        info "Run now: \"$pawbot_launcher\" agent (opens the native TUI)"
      fi
      info "For future shells: export PATH=\"$bin_dir:\$PATH\""
    fi
  elif command -v pawbot >/dev/null 2>&1; then
    info "CLI verified on PATH: $(command -v pawbot)"
    show_default_entrypoint "pawbot"
  else
    info "CLI verified through: $(pawbot_try_command)"
    show_default_entrypoint "$(pawbot_try_command)"
  fi
}

show_headless_next_steps() {
  if has_browser_session; then
    return 0
  fi
  info "Headless Linux default: $(pawbot_try_command) agent"
  info "Recommended private WebUI: $(pawbot_try_command) webui --yes --no-open --detach, then use an SSH tunnel."
  info "Remote WebUI (only after firewall and HTTPS/reverse-proxy setup): $(pawbot_try_command) webui --remote --yes --no-open --show-access --detach"
}

show_install_success

if [ "${PAWBOT_SKIP_WIZARD:-}" = "1" ]; then
  info "Skipping automatic setup because PAWBOT_SKIP_WIZARD=1."
  info "Complete setup later: $(pawbot_try_command) onboard --wizard"
  show_headless_next_steps
  exit 0
fi

if is_fresh_pawbot_install && has_browser_session; then
  if run_pawbot webui --help >/dev/null 2>&1; then
    info "Starting pawbot WebUI..."
    info "Configure your first provider and model in Settings > Models."
    info "Run this later: $(pawbot_try_command)"
    run_pawbot webui --yes
    exit 0
  fi
  info "The installed release does not support pawbot webui yet."
  info "Falling back to the setup wizard..."
fi

if [ -t 0 ]; then
  info "Starting setup wizard..."
  run_pawbot onboard --wizard
elif : 2>/dev/null < /dev/tty; then
  info "Starting setup wizard..."
  run_pawbot onboard --wizard < /dev/tty
else
  info "Skipping setup wizard because no interactive terminal is available."
  info "Run this later: $(pawbot_try_command) onboard --wizard"
fi

if has_browser_session; then
  info "Done. Open the WebUI with: $(pawbot_try_command)"
  info "For the terminal/TUI client, run: $(pawbot_try_command) agent"
else
  info "Done. Secure default: $(pawbot_try_command) agent"
fi
show_headless_next_steps
