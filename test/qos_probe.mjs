// Test driver for test_winqos.py: forks a REAL kokoro worker through TTSService
// with the sidecar's hook (tts-qos.js), kills it to force a respawn, and prints
// each worker pid once its opt-out has been applied. Stays alive until stdin
// closes so the test can read the state back from outside.
import { TTSService } from '../daemon/engines/tts/tts-service.js';
import { applyHighQos } from '../daemon/engines/tts/tts-qos.js';

const svc = new TTSService();
const applied = [];
svc.onWorkerSpawn = (pid) => { applied.push(applyHighQos(pid).then(() => pid)); };
const first = svc._spawnWorker(0);
svc._workers.push(first);
const pid1 = await applied[0];
console.log('PID ' + pid1);
first.worker.kill();
while (applied.length < 2) await new Promise((r) => setTimeout(r, 50));
const pid2 = await applied[1];
console.log('PID ' + pid2);
process.stdin.resume();
process.stdin.on('end', () => { svc.shutdown(); process.exit(0); });
