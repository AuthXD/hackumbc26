// Frees the dev ports (8000 API, 5173 web) if a previous run was left behind.
import { execSync } from "node:child_process";

for (const port of [8000, 5173]) {
  try {
    if (process.platform === "win32") {
      const out = execSync(`netstat -ano | findstr :${port}`, { encoding: "utf8" });
      const pids = new Set(
        out.split("\n").filter((l) => l.includes("LISTENING")).map((l) => l.trim().split(/\s+/).pop()),
      );
      for (const pid of pids) if (pid && pid !== "0") execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    } else {
      execSync(`lsof -ti tcp:${port} | xargs -r kill -9`, { stdio: "ignore" });
    }
  } catch {
    // nothing listening
  }
}
console.log("Ports 8000 and 5173 are free.");
