// Regenerates src/api/openapi.json and src/api/schema.d.ts from the backend's FastAPI routers.
// Picks the backend's virtualenv interpreter when there is one, else `python` (or `python3`) on PATH.
import { spawnSync } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backend = resolve(frontend, '..', 'backend');
const exporter = join(backend, 'scripts', 'export_openapi.py');
const jsonOut = join(frontend, 'src', 'api', 'openapi.json');
const typesOut = join(frontend, 'src', 'api', 'schema.d.ts');

function pickPython() {
  const candidates = [join(backend, '.venv', 'bin', 'python'), join(backend, '.venv', 'Scripts', 'python.exe')].filter(
    (candidate) => existsSync(candidate),
  );
  return [...candidates, 'python', 'python3'];
}

let exported = null;
for (const python of pickPython()) {
  const result = spawnSync(python, [exporter], { cwd: backend, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (result.status === 0 && result.stdout.trim().startsWith('{')) {
    exported = result.stdout;
    console.log(`Exported the OpenAPI schema with ${python}`);
    break;
  }
  if (result.error === undefined && result.stderr) {
    console.error(`${python} failed:\n${result.stderr}`);
  }
}

if (exported === null) {
  console.error('Could not export the OpenAPI schema: no working Python with the backend installed.');
  process.exit(1);
}

writeFileSync(jsonOut, exported.replace(/\r\n/g, '\n'));

const bin = join(frontend, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');
const generated = spawnSync(process.execPath, [bin, jsonOut, '-o', typesOut], { cwd: frontend, stdio: 'inherit' });
process.exit(generated.status ?? 1);
