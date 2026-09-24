import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

function projectVersion(): string {
  const pyprojectPath = fileURLToPath(
    new URL("../pyproject.toml", import.meta.url),
  );
  const lines = readFileSync(pyprojectPath, "utf8").split(/\r?\n/);
  let inProjectSection = false;

  for (const line of lines) {
    const section = line.match(/^\s*\[([^\]]+)]\s*(?:#.*)?$/);
    if (section) {
      if (inProjectSection) break;
      inProjectSection = section[1] === "project";
      continue;
    }

    if (inProjectSection) {
      const version = line.match(/^\s*version\s*=\s*["']([^"']+)["']/);
      if (version) return version[1];
    }
  }

  throw new Error(`Missing [project].version in ${pyprojectPath}`);
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(projectVersion()),
  },
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: [["text", { skipFull: false }], "html"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/domain.ts", "src/main.tsx"],
      thresholds: {
        // The React shell and the API client are fully covered. controller.ts is
        // still the untested majority of the codebase, so the global bar is set
        // to where the behaviour suite actually reaches today and is meant to be
        // raised with every batch of the refactor rather than left here.
        statements: 75,
        branches: 60,
        functions: 82,
        lines: 80,
      },
    },
  },
});
