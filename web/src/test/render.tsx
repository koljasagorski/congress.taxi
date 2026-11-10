import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "../App";
import { AuthProvider } from "../context/AuthContext";
import { ConfigProvider } from "../context/ConfigContext";
import { I18nProvider } from "../i18n";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

/** Rendert die komplette App mit Router, Konfiguration, Auth und i18n an einer Startadresse. */
export function renderApp(route = "/") {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[route]}>
        <ConfigProvider>
          <AuthProvider>
            <App />
            <LocationProbe />
          </AuthProvider>
        </ConfigProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}
