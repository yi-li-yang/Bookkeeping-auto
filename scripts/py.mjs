// Resolve the project virtualenv's Python across platforms so package.json
// scripts don't hardcode a Windows path, then forward all arguments to it.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const candidates = [
  join(repoRoot, ".venv", "Scripts", "python.exe"),
  join(repoRoot, ".venv", "bin", "python"),
];
const python = candidates.find(existsSync) ?? "python";

spawn(python, process.argv.slice(2), { stdio: "inherit", cwd: repoRoot })
  .on("exit", (code) => process.exit(code ?? 0));
