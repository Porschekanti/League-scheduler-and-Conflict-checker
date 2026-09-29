/**
 * Compile src/app.ts -> app.js with nothing installed.
 *
 * Node's own `module.stripTypeScriptTypes` does the work, so there is no
 * TypeScript package, no node_modules, and no lockfile in this project. Types
 * are erased (replaced by whitespace) rather than transformed, which is why
 * src/app.ts avoids `enum`, `namespace` and constructor parameter properties —
 * those are the constructs that would need real codegen.
 *
 *     node frontend/build.mjs          # compile once
 *     node frontend/build.mjs --watch  # recompile on save
 *
 * Requires Node 22.13+ (this machine has the API; `node -v` to confirm).
 */
import { stripTypeScriptTypes } from "node:module";
import { readFileSync, writeFileSync, watchFile } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "src", "app.ts");
const OUT = join(here, "app.js");

const BANNER = `// GENERATED FILE — do not edit.
// Source: src/app.ts   Build: node frontend/build.mjs
`;

function build() {
  const ts = readFileSync(SRC, "utf8");
  let js;
  try {
    js = stripTypeScriptTypes(ts, { mode: "strip", sourceMap: false });
  } catch (err) {
    console.error(`✗ ${err.message}`);
    return false;
  }
  writeFileSync(OUT, BANNER + js);
  const kb = (Buffer.byteLength(js) / 1024).toFixed(1);
  console.log(`✓ app.js  ${kb} kB  (${new Date().toLocaleTimeString()})`);
  return true;
}

const ok = build();

if (process.argv.includes("--watch")) {
  console.log("watching src/app.ts …");
  watchFile(SRC, { interval: 300 }, build);
} else if (!ok) {
  process.exit(1);
}
