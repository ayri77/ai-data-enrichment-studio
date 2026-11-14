import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { UsagePage } from "./components/UsagePage";
import "./i18n";
import "./styles.css";
import "./workspaces.css";

// The owner's usage summary lives beside the studio, at #usage, not inside it.
function Root() {
    const [hash, setHash] = useState(window.location.hash);
    useEffect(() => {
        const changed = () => setHash(window.location.hash);
        window.addEventListener("hashchange", changed);
        return () => window.removeEventListener("hashchange", changed);
    }, []);
    return hash === "#usage" ? <UsagePage /> : <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <Root />
    </React.StrictMode>,
);
