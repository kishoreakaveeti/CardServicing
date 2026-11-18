#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
root=/opt/cardservicing
cd "$root/app"
exec 9>"$root/operation.lock"
flock -w 30 9 || { echo 'Another deployment/lifecycle operation is running.' >&2; exit 1; }
export COMPOSE_PARALLEL_LIMIT=1
app=(docker compose --env-file "$root/state/.env.azure" -f compose.azure.yaml)
edge=(docker compose --env-file "$root/state/.env.azure" -f infra/azure/compose.proxy.yaml)
start_app() {
    "${app[@]}" up -d --wait --wait-timeout 360 identity qdrant cards-api fincrime-api fincrime-agent mcp-server
    "${app[@]}" up -d --wait --wait-timeout 600
    "${edge[@]}" up -d --wait --wait-timeout 120
}
case "${1:-}" in
    deploy)
        "${app[@]}" config --quiet
        "${edge[@]}" config --quiet
        "${app[@]}" build
        # Verify actual Nginx envsubst configuration before publishing a proxy.
        "${app[@]}" run --rm --no-deps frontend sh -c '/docker-entrypoint.d/20-envsubst-on-templates.sh && nginx -t'
        "${app[@]}" up -d --wait --wait-timeout 360 identity qdrant
        "${app[@]}" run --rm --no-deps --workdir /data --entrypoint python cards-agent -m card_chat.policy.pipeline.cli ingest
        # Runtime source, environment and bind paths can change between releases.
        "${app[@]}" up -d --force-recreate --wait --wait-timeout 600
        # Validate DNS from the VM itself; no certificate issuance before both
        # owned public hostnames resolve to this VM's static IP.
        python3 infra/azure/check_dns.py "$root/state/deployment.json"
        "${edge[@]}" run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
        "${edge[@]}" up -d --wait --wait-timeout 120
        ;;
    start) start_app ;;
    restart)
        "${edge[@]}" stop
        "${app[@]}" stop --timeout 60
        start_app
        ;;
    stop)
        "${edge[@]}" stop --timeout 30
        "${app[@]}" stop --timeout 60
        ;;
    status)
        "${app[@]}" ps
        "${edge[@]}" ps
        df -h /var/lib/docker
        free -h
        ;;
    verify)
        trap 'status=$?; trap - ERR; python3 infra/azure/report.py "$root/state/smoke-report.json" || true; exit "$status"' ERR
        rm -f -- "$root/state/"{public,internal,agent,restart,blob,isolation}-report.json "$root/state/smoke-customer.json"
        # Public tests use real HTTPS; no insecure TLS/cookie exceptions.
        "${app[@]}" run --rm --no-deps --user 0 --entrypoint python \
            -v "$root:/deployment" fincrime-api /deployment/app/infra/azure/smoke.py
        "${app[@]}" exec -T experience python - < infra/azure/verify_internal.py > "$root/state/internal-report.json"
        "${app[@]}" exec -T cards-agent python - < infra/azure/verify_internal.py > "$root/state/agent-report.json"
        python3 infra/azure/verify_restart.py
        python3 infra/azure/verify_isolation.py
        "${app[@]}" run --rm --no-deps --user 0 --entrypoint python \
            -v "$root:/deployment" fincrime-api /deployment/app/infra/azure/verify_blob.py
        python3 infra/azure/report.py "$root/state/smoke-report.json"
        ;;
    backup) python3 infra/azure/backup_data.py backup "${2:?age recipient required}" ;;
    restore) python3 infra/azure/backup_data.py restore "${2:?backup path required}" "${3:?age identity path required}" ;;
    *) echo 'Expected deploy/start/restart/stop/status/verify/backup/restore' >&2; exit 2 ;;
esac
