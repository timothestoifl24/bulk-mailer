// Copies screenshots/ and assets/ from the repository root into docs/public/,
// which VitePress serves at the site root.
//
// Why copy rather than move: README.md references both directories from the
// repository root, and GitHub renders that README. Keeping one tracked copy at
// the root and mirroring it here means the two can never drift. The copies
// under docs/public/ are gitignored.
//
// Run automatically by the predocs:dev and predocs:build npm scripts.

import { cp, mkdir, rm, stat } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const PUBLIC = join(ROOT, "docs", "public");

// [source at the repository root, destination under docs/public/]
//
// Everything goes under media/ rather than straight into public/. Two
// collisions that namespace avoids:
//   - VitePress emits its own bundles to /assets/, so mirroring assets/ there
//     would drop our files into a directory the build owns.
//   - cleanUrls serves the Screenshots page at /screenshots. A /screenshots/
//     directory alongside it makes that request ambiguous, and which one a
//     static host serves is not something to gamble a page on.
const MIRRORED = [
  ["screenshots", "media/screenshots"],
  ["assets", "media/brand"],
];

const exists = async (path) => {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
};

await mkdir(PUBLIC, { recursive: true });

for (const [source, name] of MIRRORED) {
  const from = join(ROOT, source);
  const to = join(PUBLIC, name);

  if (!(await exists(from))) {
    console.error(`mirror: ${source}/ does not exist - run this from a checkout.`);
    process.exit(1);
  }

  // Removed first so a file deleted at the root does not linger here and get
  // published from a stale copy.
  await rm(to, { recursive: true, force: true });
  await mkdir(dirname(to), { recursive: true });
  await cp(from, to, { recursive: true });
  console.log(`mirror: ${source}/ -> ${relative(ROOT, to).replace(/\\/g, "/")}/`);
}
