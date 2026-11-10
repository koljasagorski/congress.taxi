import { Route, Routes } from "react-router";
import { Layout, RequireAuth } from "./components/Layout";
import { DeleteAccountPage } from "./pages/DeleteAccount";
import { HomePage } from "./pages/Home";
import { ImprintPage, PrivacyPage } from "./pages/Legal";
import { LoginPage } from "./pages/Login";
import { MyRidesPage } from "./pages/MyRides";
import { NotFoundPage } from "./pages/NotFound";
import { OfferRidePage } from "./pages/OfferRide";
import { RegisterPage } from "./pages/Register";
import { SearchRidePage } from "./pages/SearchRide";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="anmelden" element={<LoginPage />} />
        <Route path="registrieren" element={<RegisterPage />} />
        <Route path="datenschutz" element={<PrivacyPage />} />
        <Route path="impressum" element={<ImprintPage />} />
        <Route element={<RequireAuth />}>
          <Route path="fahrt-anbieten" element={<OfferRidePage />} />
          <Route path="mitfahrt-suchen" element={<SearchRidePage />} />
          <Route path="meine-fahrten" element={<MyRidesPage />} />
        </Route>
        <Route path="konto-loeschen" element={<DeleteAccountPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
