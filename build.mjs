/**
 * build.mjs — Bundles the TypeScript source into a single ESM script.
 *
 * The output (scripts/relay-forward.mjs) is a self-contained Node.js module
 * with zero runtime dependencies. It is committed to the repository so that
 * users never need to run this build script.
 *
 * Usage: node build.mjs
 */

import * as esbuild from "esbuild";

const result = await esbuild.build({
  entryPoints: ["src/index.ts"],
  bundle: true,
  platform: "node",
  target: "node18",
  format: "esm",
  outfile: "scripts/relay-forward.mjs",
  banner: {
    js: "#!/usr/bin/env node",
  },
  external: [
    // Keep Node.js built-ins external — they're available at runtime
    "node:*",
    "crypto",
    "fs",
    "path",
    "url",
  ],
  minify: false,
  sourcemap: false,
  logLevel: "info",
});

if (result.errors.length > 0) {
  console.error("Build failed with errors.");
  process.exit(1);
}

if (result.warnings.length > 0) {
  console.warn("Build completed with warnings:");
  for (const w of result.warnings) {
    console.warn(`  ${w.text}`);
  }
}

console.log("Build completed: scripts/relay-forward.mjs");
