import fs from 'node:fs';
import { init } from '@flue/runtime';
import { start } from '@flue/runtime/node';
import { CodingAgent, ReviewAgent, PlanningAgent, SpecialistAgent } from './roles.mjs';
import { createGateway } from './provider.mjs';

const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
let runtime;
let agent;
try {
  const roles = { coder: CodingAgent, reviewer: ReviewAgent, planner: PlanningAgent, specialist: SpecialistAgent };
  if (!Object.hasOwn(roles, spec.role)) throw new Error('Invalid role');
  const remaining = Math.floor(spec.deadline * 1000 - Date.now());
  if (remaining <= 0) throw new Error('Deadline expired');
  runtime = await start({
    agents: Object.values(roles),
    providers: [createGateway(spec.base_url, spec.role)], env: {},
  });
  agent = init(roles[spec.role]);
  const receipt = await agent.dispatch(spec.task);
  const reply = await agent.read(receipt, { signal: AbortSignal.timeout(remaining) });
  if (!reply.text.trim() || Buffer.byteLength(reply.text) > 256 * 1024) {
    throw new Error('Invalid final reply');
  }
  process.stdout.write(JSON.stringify({
    schema_version: 'flue-result/v1', role: spec.role,
    submission_id: receipt.submissionId, message: reply.text,
    usage: reply.metadata?.usage ?? null, status: 'completed',
  }) + '\n');
} catch {
  await agent?.abort().catch(() => undefined);
  // Raw provider/runtime errors may contain private upstream information.
  process.stderr.write('Flue agent execution failed\n');
  process.exitCode = 1;
} finally {
  await runtime?.stop();
}
