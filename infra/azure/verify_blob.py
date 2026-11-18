"""Prove real per-service Blob uploads using VM managed identity, not account keys."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
from card_servicing.telemetry import AzureBlobTelemetry

STATE=Path('/deployment/state')


def main():
    results=[]
    client=None
    credential=None
    try:
        credential=ManagedIdentityCredential(connection_timeout=3,read_timeout=3,retry_total=0)
        client=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=credential,
            connection_timeout=3,read_timeout=3,retry_total=0)
        container=client.get_container_client(os.environ['AZURE_STORAGE_CONTAINER'])
        # New role assignments can take time to propagate. Verify the data
        # plane before emitting the marker; never fall back to account keys.
        for attempt in range(48):
            try:
                container.get_container_properties()
                break
            except Exception:
                if attempt == 47:
                    raise
                time.sleep(5)
        marker=uuid4().hex
        probe_names=[]
        class ProbeClient:
            def get_blob_client(self, container_name, name):
                probe_names.append(name)
                return client.get_blob_client(container_name, name)
            def close(self):
                pass  # The verifier owns this shared SDK client.
        sink=AzureBlobTelemetry(dict(os.environ),client_factory=ProbeClient)
        sink.start()
        sink.emit('trace','deployment_probe',service='deployment-verifier',trace_id=marker)
        sink.close()
        # Include previous hour around UTC partition boundaries; cap the scan.
        now=datetime.now(timezone.utc)
        prefixes={f'events/{(now-timedelta(hours=hour)):%Y/%m/%d/%H}/' for hour in (0,1)}
        required={'cards','agent','fincrime-agent','mcp','experience','fincrime'}
        seen=set()
        downloaded=set()
        probe=False
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            # Probe names come from the actual adapter's SDK calls. Do not rely
            # on a random UUID sorting into the first listing page.
            for name in list(probe_names):
                try:
                    events=client.get_blob_client(os.environ['AZURE_STORAGE_CONTAINER'],name).download_blob().readall()
                    probe=probe or any(json.loads(line).get('trace_id')==marker for line in events.splitlines())
                except Exception:
                    pass
            for prefix in prefixes:
                import itertools
                blobs=list(itertools.islice(container.list_blobs(name_starts_with=prefix,results_per_page=500),5000))
                # Scan newest records only; refuse unbounded downloads.
                blobs=sorted(blobs,key=lambda b:b.last_modified,reverse=True)[:300]
                for blob in blobs:
                    if blob.size>2_000_000 or blob.name in downloaded:
                        continue
                    downloaded.add(blob.name)
                    for line in container.download_blob(blob.name).readall().splitlines():
                        event=json.loads(line)
                        seen.add(event.get('service'))
                        probe=probe or event.get('trace_id')==marker
            if probe and required<=seen:
                break
            time.sleep(5)
        assert probe, 'Managed identity upload marker not found'
        results.append(dict(check='managed-identity-blob',status='PASS',evidence='Uploaded NDJSON and retrieved its unique correlation marker using VM managed identity'))
        missing=required-seen
        results.append(dict(check='per-service-blob',status='FAIL' if missing else 'PASS',
            evidence='Missing service telemetry: '+','.join(sorted(missing)) if missing else 'Actual Blob events found for all six instrumented application services'))
    except Exception as exc:
        results.append(dict(check='managed-identity-blob',status='FAIL',evidence=type(exc).__name__))
    finally:
        if client:
            client.close()
        if credential:
            credential.close()
        (STATE/'blob-report.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
