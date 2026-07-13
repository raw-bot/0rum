#!/usr/bin/env bash
set -u

PROJECT="/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading"
LABEL="com.0rum.llm-paper"
DOMAIN="gui/$(id -u)"
SOURCE="$PROJECT/config/com.0rum.llm-paper.plist"
DESTINATION="$HOME/Library/LaunchAgents/$LABEL.plist"

install_agent() {
  mkdir -p "$HOME/Library/LaunchAgents"
  /usr/bin/install -m 600 "$SOURCE" "$DESTINATION"
  plutil -lint "$DESTINATION"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl enable "$DOMAIN/$LABEL"
  launchctl bootstrap "$DOMAIN" "$DESTINATION"
  launchctl kickstart "$DOMAIN/$LABEL"
}

enable_agent() {
  launchctl enable "$DOMAIN/$LABEL"
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl kickstart "$DOMAIN/$LABEL"
  else
    launchctl bootstrap "$DOMAIN" "$DESTINATION"
  fi
}

disable_agent() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl disable "$DOMAIN/$LABEL"
}

status_agent() {
  launchctl print "$DOMAIN/$LABEL"
}

uninstall_agent() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$DESTINATION"
}

case "${1:-status}" in
  install) install_agent ;;
  enable) enable_agent ;;
  disable) disable_agent ;;
  status) status_agent ;;
  uninstall) uninstall_agent ;;
  *)
    echo "usage: $0 {install|enable|disable|status|uninstall}" >&2
    exit 2
    ;;
esac
