#!/usr/bin/env node
import { buildGateContext, classifyPrompt } from './gate.mjs';
if (process.env.AORCH_WORKER === '1' || process.env.AORCH_VERIFIER === '1') process.exit(0);
try {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8').trim();
  const input = raw ? JSON.parse(raw) : {};
  process.stdout.write(JSON.stringify({ hookSpecificOutput: {
    hookEventName: 'UserPromptSubmit',
    additionalContext: buildGateContext(classifyPrompt(input.prompt))
  } }));
} catch (error) {
  process.stderr.write(`adaptive-orchestrator hook input error: ${error.message}\n`);
  process.exitCode = 1;
}
