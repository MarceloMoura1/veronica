import assert from 'node:assert/strict';
import { appendHistory, appendSystemHistories, formatBytes, formatRate, formatUptime, statusTone } from '../../src/systemStatus.mjs';

assert.deepEqual(appendHistory([1, 2, 3], 4, 3), [2, 3, 4]);
assert.deepEqual(appendHistory([1], null, 3), [1]);
let histories = { cpu: [], memory: [], application: [], disk: [], network: [], gpu: [] };
for (let index = 0; index < 75; index += 1) {
    histories = appendSystemHistories(histories, {
        cpu: { percent: index }, memory: { percent: index }, disk: { percent: index },
        application: { memory_bytes: index * 1024 },
        network: { download_bps: index, upload_bps: index }, gpu: { available: false },
    });
}
assert.equal(histories.cpu.length, 60);
assert.equal(histories.cpu[0], 15);
assert.equal(histories.network.at(-1), 148);
assert.equal(histories.application.length, 60);
assert.equal(histories.application.at(-1), 74 * 1024);
assert.equal(formatBytes(12 * 1024 ** 3), '12.0 GB');
assert.equal(formatRate(2 * 1024 ** 2), '2.0 MB/s');
assert.equal(formatUptime(3 * 86400 + 4 * 3600 + 12 * 60), '3d 4h 12m');
assert.equal(statusTone('healthy'), 'healthy');
assert.equal(statusTone('warning'), 'warning');
assert.equal(statusTone('critical'), 'critical');
assert.equal(statusTone(undefined), 'unavailable');
console.log('frontend system status tests: passed');
