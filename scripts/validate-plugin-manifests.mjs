#!/usr/bin/env node

import { readdir, readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const expectedName = "dfrysinger-dreaming";
const expectedVersion = "1.0.2";
const expectedSkills = [
  "skill-review",
  "skill-curator",
  "memory-curator",
  "skill-manage",
].sort();

async function readJson(path) {
  return JSON.parse(await readFile(resolve(root, path), "utf8"));
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const [claude, claudeMarket, codex, codexMarket] = await Promise.all([
  readJson(".claude-plugin/plugin.json"),
  readJson(".claude-plugin/marketplace.json"),
  readJson(".codex-plugin/plugin.json"),
  readJson(".agents/plugins/marketplace.json"),
]);
for (const [label, manifest] of [["Claude/Copilot", claude], ["Codex", codex]]) {
  assert(manifest.name === expectedName, `${label} name mismatch`);
  assert(manifest.version === expectedVersion, `${label} version mismatch`);
  assert(manifest.description === claude.description, `${label} description mismatch`);
}
assert(codex.skills === "./skills/", "Codex must expose ./skills/");
for (const market of [claudeMarket, codexMarket]) {
  assert(market.name === expectedName, "marketplace name mismatch");
  assert(market.plugins?.some(({ name }) => name === expectedName), "marketplace plugin missing");
}
const claudeEntry = claudeMarket.plugins.find(({ name }) => name === expectedName);
assert(claudeMarket.metadata?.version === expectedVersion, "Claude marketplace version mismatch");
assert(claudeEntry.version === expectedVersion && claudeEntry.source === "./", "Claude marketplace entry mismatch");
const codexEntry = codexMarket.plugins.find(({ name }) => name === expectedName);
assert(codexEntry.source?.source === "local" && codexEntry.source.path === "./", "Codex marketplace source mismatch");

const directories = (await readdir(resolve(root, "skills"), { withFileTypes: true }))
  .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
  .map((entry) => entry.name)
  .sort();
assert(JSON.stringify(directories) === JSON.stringify(expectedSkills), "repository must contain exactly four owned skills");
assert(
  JSON.stringify([...claude.skills].map((path) => path.replace("./skills/", "")).sort()) ===
    JSON.stringify(expectedSkills),
  "Claude/Copilot manifest must export exactly the four owned skills",
);
for (const directory of directories) {
  const path = resolve(root, "skills", directory, "SKILL.md");
  assert((await stat(path)).isFile(), `${directory} is missing SKILL.md`);
  const content = await readFile(path, "utf8");
  const frontmatter = content.match(/^---\n([\s\S]*?)\n---\n/);
  assert(frontmatter, `${directory} has malformed frontmatter`);
  assert(new RegExp(`^name:\\s*${directory}\\s*$`, "m").test(frontmatter[1]), `${directory} name mismatch`);
  assert(/^description:\s*\S/m.test(frontmatter[1]), `${directory} description missing`);
}
console.log("Plugin manifests are consistent.");
