// Cloudflare Builds supplies the non-secret VPC ID. Never load or copy .env.
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export function workerConfiguration(serviceId, workerName = 'gardifund') {
  if (typeof serviceId !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(serviceId)
      || serviceId.replaceAll('-', '') === '0'.repeat(32)) {
    throw new Error('Set GARDIFUND_VPC_SERVICE_ID to the VPC Service UUID for 127.0.0.1:8766.');
  }
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(workerName)) {
    throw new Error('Use a valid Cloudflare Worker name.');
  }
  return {
    name: workerName, main: 'worker.js', compatibility_date: '2026-11-08',
    workers_dev: true, preview_urls: false, observability: { enabled: false },
    vpc_services: [{ binding: 'GARDIFUND_ORIGIN', service_id: serviceId }],
  };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const configuration = workerConfiguration(process.env.GARDIFUND_VPC_SERVICE_ID, process.env.GARDIFUND_WORKER_NAME || 'gardifund');
  const destination = new URL('../deploy/worker/wrangler.generated.json', import.meta.url);
  await writeFile(destination, JSON.stringify(configuration, null, 2) + '\n', 'utf8');
  console.log(`Generated ${fileURLToPath(destination)} for the read-only gateway; no static app assets or credentials included.`);
}
