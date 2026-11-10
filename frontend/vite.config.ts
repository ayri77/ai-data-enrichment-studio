import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
    plugins: [react(), tailwindcss()],
    server: {
        port: 5173,
        strictPort: true,
        proxy: {
            "/api": {
                target: process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8010",
                // Preserve the browser authority for the backend's exact Host/Origin check.
                changeOrigin: false,
                xfwd: false,
            },
        },
    },
    test: {
        environment: "jsdom",
        testTimeout: 15_000,
        setupFiles: ["./src/test-setup.ts"],
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
