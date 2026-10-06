#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER="${1:?Pass the SSH deploy username}"
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root or with sudo: bootstrap-server.sh <deploy-user>" >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify Linux distribution" >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu && "${ID:-}" != debian ]]; then
  echo "Supported server distributions: Ubuntu and Debian" >&2
  exit 1
fi
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  echo "Deploy user does not exist: $DEPLOY_USER" >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
arch="$(dpkg --print-architecture)"
codename="${VERSION_CODENAME:-}"
if [[ -z "$codename" ]]; then
  echo "OS release does not provide VERSION_CODENAME" >&2
  exit 1
fi
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
  "$arch" "$ID" "$codename" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "$DEPLOY_USER"
deploy_home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
if [[ -z "$deploy_home" ]]; then
  echo "Cannot resolve the deploy user's home directory" >&2
  exit 1
fi
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$deploy_home/solaris-atlas"
echo "Docker is ready. The deploy user must reconnect once to activate docker-group membership."
