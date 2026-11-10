import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { AuthProvider } from "./context/AuthContext";
import { ConfigProvider } from "./context/ConfigContext";
import { I18nProvider } from "./i18n";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Wurzelelement #root fehlt");

createRoot(root).render(
  <StrictMode>
    <I18nProvider>
      <BrowserRouter>
        <ConfigProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </ConfigProvider>
      </BrowserRouter>
    </I18nProvider>
  </StrictMode>,
);
