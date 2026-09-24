const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "../..");
const indexPath = path.join(root, "app/static/index.html");
const pyproject = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
let inProjectSection = false;
let projectVersion;
for (const line of pyproject.split(/\r?\n/)) {
  const section = line.match(/^\s*\[([^\]]+)]\s*(?:#.*)?$/);
  if (section) {
    if (inProjectSection) break;
    inProjectSection = section[1] === "project";
    continue;
  }
  if (inProjectSection) {
    projectVersion = line.match(/^\s*version\s*=\s*["']([^"']+)["']/)?.[1];
    if (projectVersion) break;
  }
}
assert(projectVersion, "pyproject.toml must define [project].version");
assert(fs.existsSync(indexPath), "Production frontend has not been built");
const index = fs.readFileSync(indexPath, "utf8");
assert(
  index.includes("Release Controller"),
  "Built app is missing the product title",
);

const assetPaths = [
  ...index.matchAll(/(?:src|href)="(\/assets\/[^"?]+)"/g),
].map((match) => match[1]);
assert(
  assetPaths.length >= 2,
  "Built app must reference JavaScript and CSS assets",
);
for (const assetPath of assetPaths) {
  assert(
    fs.existsSync(path.join(root, "app/static", assetPath)),
    `Missing built asset: ${assetPath}`,
  );
}
// The controller is a lazily imported chunk, so index.html never names it: its
// filename only appears inside the entry chunk. Follow those references, or half
// the shipped app goes unchecked.
const seen = new Set();
const pending = assetPaths.filter((assetPath) => assetPath.endsWith(".js"));
const sources = [];
while (pending.length) {
  const assetPath = pending.pop();
  if (seen.has(assetPath)) continue;
  seen.add(assetPath);
  const file = path.join(root, "app/static", assetPath);
  // Assets named by index.html were already required to exist above. A path
  // discovered inside a chunk may just be a string the app carries, so a miss
  // here means "not a chunk", not "broken build".
  if (!fs.existsSync(file)) continue;
  const source = fs.readFileSync(file, "utf8");
  sources.push(source);
  // Vite writes the lazy import as a relative specifier in a template literal,
  // so accept every quote style and resolve against this chunk's own directory.
  for (const match of source.matchAll(
    /["'`](\.{0,2}\/[A-Za-z0-9._/-]+\.js)["'`]/g,
  )) {
    pending.push(path.posix.resolve(path.posix.dirname(assetPath), match[1]));
  }
}
assert(
  sources.length >= 2,
  "Built app must ship the entry chunk and the lazily imported controller chunk",
);
const javascript = sources.join("\n");
assert(
  javascript.includes("Choose independent source builds"),
  "Built React app is missing the v2.2 launcher",
);
assert(
  javascript.includes("Release selected components"),
  "Built React app is missing combined deployment action",
);
// v3.0: which components exist comes from the selected project, so the shipped
// bundle must carry the project picker and the grid the controller fills.
assert(
  javascript.includes("project-selector") &&
    javascript.includes("component-grid"),
  "Built React app is missing the project picker or the component grid",
);
assert(
  javascript.includes("Loading repository"),
  "Built React app is missing build selection cards",
);
assert(
  javascript.includes("Release Worker v") &&
    javascript.includes(projectVersion),
  "Built React app version does not match pyproject.toml",
);
console.log("production frontend smoke checks passed");
