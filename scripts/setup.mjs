// One-time setup: backend virtualenv + pip install, then frontend npm install.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const win = process.platform === "win32";
const venvPython = win
  ? join(backend, ".venv", "Scripts", "python.exe")
  : join(backend, ".venv", "bin", "python");

function run(cmd, args, cwd = root) {
  console.log(`\n> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { cwd, stdio: "inherit", shell: win });
  if (r.status !== 0) {
    console.error(`Command failed: ${cmd} ${args.join(" ")}`);
    process.exit(r.status ?? 1);
  }
}

function works(cmd, args) {
  return spawnSync(cmd, args, { stdio: "ignore", shell: win }).status === 0;
}

if (!existsSync(venvPython)) {
  // Prefer Python 3.12 (well-supported OpenCV wheels), then fall back to whatever is on PATH.
  const candidates = win
    ? [["py", ["-3.12"]], ["py", ["-3.11"]], ["python", []]]
    : [["python3.12", []], ["python3", []]];
  const found = candidates.find(([cmd, pre]) => works(cmd, [...pre, "--version"]));
  if (!found) {
    console.error("No Python found. Install Python 3.12 from python.org and re-run.");
    process.exit(1);
  }
  run(found[0], [...found[1], "-m", "venv", ".venv"], backend);
}

run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], backend);
run("npm", ["install"], join(root, "frontend"));
console.log("\nSetup complete. Start with: npm run dev  (then open http://localhost:5173)");
