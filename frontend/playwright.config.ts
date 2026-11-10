import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./e2e",
    timeout: 60_000,
    workers: 1,
    use: { baseURL: "http://127.0.0.1:5174", trace: "retain-on-failure" },
    webServer: [
        {
            command:
                "uv run --locked uvicorn tests.browser.fixture_server:app --host 127.0.0.1 --port 8011",
            cwd: "..",
            url: "http://127.0.0.1:8011/api/health",
            reuseExistingServer: false,
        },
        {
            command: "npm run dev -- --port 5174",
            url: "http://127.0.0.1:5174",
            env: { API_PROXY_TARGET: "http://127.0.0.1:8011" },
            reuseExistingServer: false,
        },
    ],
});
