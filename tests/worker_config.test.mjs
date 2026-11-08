import assert from 'node:assert/strict';
import test from 'node:test';
import { workerConfiguration } from '../scripts/build-worker-config.mjs';

const serviceId = '12345678-1234-1234-1234-123456789abc';

test('the Cloudflare deployment uses the gateway and a fixed service rather than static app assets', () => {
  const config = workerConfiguration(serviceId);
  assert.equal(config.name, 'gardifund');
  assert.equal(config.main, 'worker.js');
  assert.deepEqual(config.vpc_services, [{ binding: 'GARDIFUND_ORIGIN', service_id: serviceId }]);
  assert.equal(config.preview_urls, false);
  assert.equal(config.workers_dev, true);
  for (const key of ['assets', 'vars', 'vpc_networks', 'routes']) assert.equal(key in config, false);
});

test('missing, placeholder and malformed service IDs fail before any deployment is possible', () => {
  for (const value of [undefined, '', 'REPLACE-WITH-YOUR-VPC-SERVICE-ID', '00000000-0000-0000-0000-000000000000',
    'https://127.0.0.1:8765', serviceId + '\n', 'token=private']) {
    assert.throws(() => workerConfiguration(value), /GARDIFUND_VPC_SERVICE_ID/);
  }
});

test('configuration does not copy backend credentials or allow a malformed Worker name', () => {
  const previous = process.env.PRODUCTION_API_KEY;
  process.env.PRODUCTION_API_KEY = 'synthetic-backend-secret';
  try {
    assert.doesNotMatch(JSON.stringify(workerConfiguration(serviceId)), /synthetic-backend-secret|PRODUCTION_API_KEY/);
    for (const name of ['gardifund/workspace', 'https://other.example', '-invalid', 'INVALID', 'x'.repeat(64)]) {
      assert.throws(() => workerConfiguration(serviceId, name), /Worker name/);
    }
  } finally {
    if (previous === undefined) delete process.env.PRODUCTION_API_KEY;
    else process.env.PRODUCTION_API_KEY = previous;
  }
});
