import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./e2e",
    timeout: 120_000,
    workers: 1,
    use: {
        viewport: { width: 1440, height: 900 },
        baseURL: "http://127.0.0.1:5174",
        trace: "retain-on-failure",
    },
    projects: [
        { name: "workflow", testIgnore: /provider-settings\.spec\.ts$/ },
        {
            name: "provider-settings",
            testMatch: /provider-settings\.spec\.ts$/,
            use: {
                baseURL: "http://127.0.0.1:5173",
                trace: "off",
                screenshot: "off",
                video: "off",
            },
        },
    ],
    webServer: [
        {
            command:
                "uv run --locked python -m tests.browser.provider_fixture_server --allow-network",
            cwd: "..",
            url: "http://127.0.0.1:8010/api/health",
            reuseExistingServer: false,
            env: { OPENAI_API_KEY: "", OPENAI_MODEL: "" },
        },
        {
            command: "npm run dev -- --port 5173",
            url: "http://127.0.0.1:5173",
            reuseExistingServer: false,
            env: { API_PROXY_TARGET: "http://127.0.0.1:8010" },
        },
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
