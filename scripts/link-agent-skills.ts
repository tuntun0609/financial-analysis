#!/usr/bin/env bun

import {
  lstatSync,
  mkdirSync,
  readdirSync,
  statSync,
  symlinkSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

const scriptPath = process.argv[1];

if (typeof scriptPath !== "string") {
  throw new Error("无法确定当前脚本路径");
}

const scriptDir = dirname(resolve(scriptPath));
const repoRoot = resolve(scriptDir, "..");
const sourceDir = join(repoRoot, "skills");
const targetDir = join(repoRoot, ".agents", "skills");

function pathExists(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "ENOENT"
    ) {
      return false;
    }

    throw error;
  }
}

if (!pathExists(sourceDir) || !statSync(sourceDir).isDirectory()) {
  console.error(`找不到 skill 目录：${sourceDir}`);
  process.exit(1);
}

mkdirSync(targetDir, { recursive: true });

let created = 0;
let skipped = 0;

const skillNames = readdirSync(sourceDir, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .filter((name) => pathExists(join(sourceDir, name, "SKILL.md")))
  .sort();

for (const skillName of skillNames) {
  const skillDir = join(sourceDir, skillName);
  const target = join(targetDir, skillName);

  // lstat 能识别目标不存在的失效软链接，避免意外覆盖它。
  if (pathExists(target)) {
    console.log(`跳过（已存在）：${target}`);
    skipped += 1;
    continue;
  }

  const relativeSource = relative(targetDir, skillDir);
  symlinkSync(relativeSource, target, "dir");
  console.log(`已创建：${target} -> ${relativeSource}`);
  created += 1;
}

console.log(`完成：创建 ${created} 个，跳过 ${skipped} 个。`);
