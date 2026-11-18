import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";

const appDirectory = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function availablePort() {
  const probe = createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const address = probe.address();
  assert(address && typeof address !== "string");
  const { port } = address;
  await new Promise((resolveClose, rejectClose) =>
    probe.close((error) => (error ? rejectClose(error) : resolveClose())),
  );
  return port;
}

test("the dashboard root route responds with the foundation page", { timeout: 60_000 }, async () => {
  const port = await availablePort();
  const nextCli = resolve(appDirectory, "node_modules/next/dist/bin/next");
  const server = spawn(
    process.execPath,
    [nextCli, "dev", "--hostname", "127.0.0.1", "--port", String(port)],
    { cwd: appDirectory, stdio: "ignore" },
  );

  let response;
  try {
    const deadline = Date.now() + 50_000;
    while (Date.now() < deadline) {
      if (server.exitCode !== null) {
        throw new Error(`Next.js server exited with code ${server.exitCode}`);
      }
      try {
        response = await fetch(`http://127.0.0.1:${port}/`, {
          signal: AbortSignal.timeout(1_000),
        });
        break;
      } catch {
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
      }
    }
  } finally {
    if (server.exitCode === null) {
      server.kill();
      await Promise.race([once(server, "exit"), new Promise((resolveDelay) => setTimeout(resolveDelay, 2_000))]);
    }
  }

  assert(response, "Next.js server did not become ready in time");
  assert.equal(response.status, 200);
  assert.match(await response.text(), /Dashboard foundation is ready\./);
});
