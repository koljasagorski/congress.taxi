import { useEffect, useRef } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../i18n";

export function Layout() {
  const { t } = useI18n();
  const { status, user, logout } = useAuth();
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);
  const firstRender = useRef(true);

  // Bei Seitenwechsel den Fokus in den Inhalt setzen (Screenreader kündigen die neue Seite an).
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [location.pathname]);

  return (
    <>
      <a className="skip-link" href="#main">
        {t("app.skip")}
      </a>
      <header className="site-header">
        <div className="container header-inner">
          <Link to="/" className="brand">
            {t("app.name")}
          </Link>
          <nav aria-label={t("nav.label")}>
            <ul className="nav-list">
              <li>
                <NavLink to="/" end>
                  {t("nav.home")}
                </NavLink>
              </li>
              <li>
                <NavLink to="/fahrt-anbieten">{t("nav.offer")}</NavLink>
              </li>
              <li>
                <NavLink to="/mitfahrt-suchen">{t("nav.search")}</NavLink>
              </li>
              <li>
                <NavLink to="/meine-fahrten">{t("nav.mine")}</NavLink>
              </li>
              {status === "authed" ? (
                <li>
                  <button type="button" className="link-button" onClick={() => void logout()}>
                    {t("nav.logout")}
                  </button>
                </li>
              ) : (
                <>
                  <li>
                    <NavLink to="/anmelden">{t("nav.login")}</NavLink>
                  </li>
                  <li>
                    <NavLink to="/registrieren">{t("nav.register")}</NavLink>
                  </li>
                </>
              )}
            </ul>
          </nav>
          {status === "authed" && user && (
            <p className="signed-in">{t("nav.signedInAs", { name: user.display_name })}</p>
          )}
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1} className="container main">
        <Outlet />
      </main>
      <footer className="site-footer">
        <div className="container">
          <nav aria-label={t("nav.footer")}>
            <ul className="nav-list">
              <li>
                <Link to="/datenschutz">{t("nav.privacy")}</Link>
              </li>
              <li>
                <Link to="/impressum">{t("nav.imprint")}</Link>
              </li>
              {status === "authed" && (
                <li>
                  <Link to="/konto-loeschen">{t("nav.deleteAccount")}</Link>
                </li>
              )}
            </ul>
          </nav>
          <p>{t("app.footerNote")}</p>
        </div>
      </footer>
    </>
  );
}

/** Geschützte Seiten: ohne Anmeldung -> Login mit Rücksprungadresse. */
export function RequireAuth() {
  const { status } = useAuth();
  const { t } = useI18n();
  const location = useLocation();
  if (status === "loading") {
    return (
      <p role="status" className="muted">
        {t("app.loading")}
      </p>
    );
  }
  if (status === "anon") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/anmelden?next=${next}`} replace />;
  }
  return <Outlet />;
}
