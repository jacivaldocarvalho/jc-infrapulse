import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';

const root = fileURLToPath(new URL('../../', import.meta.url));
const localPython = `${root}backend/.venv/bin/python`;
const schema = process.env.OPENAPI_SCHEMA_FILE
  ? readFileSync(process.env.OPENAPI_SCHEMA_FILE, 'utf8')
  : execFileSync(
      process.env.PYTHON ?? (existsSync(localPython) ? localPython : 'python3'),
      ['-B', `${root}scripts/export-openapi.py`],
      { encoding: 'utf8', cwd: root },
    );
const output = new URL('../src/shared/api/generated.d.ts', import.meta.url);
mkdirSync(new URL('../src/shared/api/', import.meta.url), { recursive: true });
writeFileSync(
  output,
  `// Generated from the current FastAPI OpenAPI. Do not edit.\n${astToString(await openapiTS(JSON.parse(schema)))}`,
);
console.log('API types generated from FastAPI (no server or database).');
