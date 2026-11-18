#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
progress() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$1"; }
progress 'Remote VM bootstrap started: installing Ubuntu prerequisites (may take several minutes).'
# Run only by deploy.ps1 after explicitly selected Azure resources exist.
source_home=$(getent passwd "${SUDO_USER:?Run using sudo}" | cut -d: -f6)
upload="$source_home/.cardservicing-upload"
root=/opt/cardservicing
install -d -m 711 "$root" "$root/backups"
install -d -m 700 "$root/state"
# Apt repository signature verification follows Docker's official Ubuntu guide.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg python3 rsync age git
progress 'Installing Docker Engine and Docker Compose from the signed Docker repository.'
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# Dedicated VM: Docker volumes and configs live on its persistent managed OS disk,
# never the Azure temporary/resource disk. Preserve this location on every rerun.
docker_config_created=false
if [[ ! -e /etc/docker/daemon.json ]]; then
    install -d /etc/docker
    printf '%s\n' '{"data-root":"/var/lib/docker","log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
    docker_config_created=true
fi
systemctl enable docker
if [[ "$docker_config_created" == true ]]; then systemctl restart docker; else systemctl start docker; fi
if [[ $(docker info --format '{{.DockerRootDir}}') != /var/lib/docker ]]; then
    echo 'Docker data root must be /var/lib/docker on the persistent managed OS disk; refusing an implicit storage migration.' >&2
    exit 1
fi
# Azure NSG permits only operator SSH and public 80/443. No container backend is
# published on a public interface. SSH remains key-only.
printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' > /etc/ssh/sshd_config.d/10-cardservicing.conf
sshd -t
systemctl reload ssh
stage=$(mktemp -d "$root/release.XXXXXXXX")
progress 'Preparing source release; existing persistent volumes and credentials are retained.'
trap 'rm -rf -- "$stage"; rm -f -- "$upload/bootstrap.json" "$upload/release.tar.gz"' EXIT
# The tar is an allowlisted source artifact created by our deployment client.
python3 - "$upload/release.tar.gz" "$stage" <<'PY'
import sys, tarfile
from pathlib import Path
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2], filter='data')
# data_filter intentionally skips directory modes. With umask 077 this leaves
# root-only directories, which COPY preserves inside non-root application images.
# This stage contains allowlisted source only, never credentials or live volumes.
source = Path(sys.argv[2])
source.chmod(0o755)
for path in source.rglob('*'):
    if not path.is_symlink():
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
PY
# Quiesce the existing app before replacing its source. Volumes/state stay intact.
if [[ -f "$root/app/infra/azure/control.sh" ]]; then
    bash "$root/app/infra/azure/control.sh" stop
fi
install -d -m 755 "$root/app"
rsync -a --delete --exclude=data/ --exclude=.env\* --exclude=.git/ --exclude=.venv/ "$stage/" "$root/app/"
install -m 600 "$upload/bootstrap.json" "$root/state/bootstrap.json"
sha256sum "$upload/release.tar.gz" | cut -d' ' -f1 > "$root/state/source.sha256"
cd "$root/app"
# Build sequentially to keep the 16-GiB VM's peak memory bounded.
export COMPOSE_PARALLEL_LIMIT=1
# First image needs no realm credentials; the image does not contain secrets.
docker build --target runtime -f Dockerfile.fincrime-api -t card-servicing-azure-fincrime-api .
progress 'Initializing deployment-owned credentials and identity configuration (values are not logged).'
docker run --rm --user 0 --mount "type=bind,source=$root,target=/deployment" \
    --entrypoint python card-servicing-azure-fincrime-api \
    /deployment/app/infra/azure/configure.py
# All source/realm files are read-only to application containers. Only their data
# volumes are writable. Keycloak GID 0 and BFF GID 10001 get separate readers.
chown root:10001 "$root/state/identity/delegation-private.pem"
chmod 640 "$root/state/identity/delegation-private.pem"
chown root:0 "$root/state/identity/realm.json"
chmod 640 "$root/state/identity/realm.json"
chmod 644 "$root/state/identity/delegation-public.pem"
chmod 755 "$root/state/identity"
# Parent state is root-only, but Docker bind mounts are resolved by the daemon.
progress 'Building/starting the full application, ingesting policies and configuring HTTPS. Docker output follows.'
bash infra/azure/control.sh deploy
cat > /etc/systemd/system/cardservicing.service <<'UNIT'
[Unit]
Description=CardServicing Docker Compose demo
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/cardservicing/app
ExecStart=/bin/bash /opt/cardservicing/app/infra/azure/control.sh start
ExecStop=/bin/bash /opt/cardservicing/app/infra/azure/control.sh stop
TimeoutStartSec=1200
TimeoutStopSec=180
[Install]
WantedBy=multi-user.target
UNIT
progress 'Enabling automatic service recovery after reboot.'
systemctl daemon-reload
systemctl enable cardservicing
systemctl start cardservicing
rm -f "$root/state/bootstrap.json"
printf '%s\n' 'Linux deployment completed; run the public and private smoke verification.'
