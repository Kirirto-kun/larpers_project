import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { House, Plus, Monitor, LogOut, ShieldCheck } from "lucide-react";
import { api, post, setCsrf, errorText, ApiError } from "./lib/api";
import type { Session } from "./lib/types";
import { Button } from "./components/ui/button";
import { ErrorBox } from "./components/Common";
import { Logo } from "./components/Logo";
import { Landing } from "./pages/Landing";
import { Home } from "./pages/Home";
import { Contribute } from "./pages/Contribute";
import { NewJob } from "./pages/NewJob";
import { JobDetails } from "./pages/JobDetails";
export function App() {
  const [session, setSession] = useState<Session | null>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const route = useLocation();
  const navigate = useNavigate();
  const signup = route.pathname === "/register";
  const authRoute = ["/login", "/register"].includes(route.pathname);
  const requested =
    new URLSearchParams(route.search).get("next") ||
    (authRoute ? "/" : route.pathname);
  const destination =
    /^\/(?:new|contribute|jobs\/[a-zA-Z0-9_-]+|admin\/(?:nodes|jobs|benchmarks)?)?$/.test(
      requested,
    )
      ? requested
      : "/";
  const [activation, setActivation] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const started = useRef(false);
  function accept(s: Session) {
    setCsrf(s.csrf_token || "");
    setSession(s);
    setName(s.name);
    setEmail(s.email || "");
    setError("");
    setPassword("");
  }
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const hash = new URLSearchParams(location.hash.slice(1));
    const token = hash.get("token") || hash.get("invitation");
    if (token)
      history.replaceState(null, "", location.pathname + location.search);
    (async () => {
      try {
        const s = token
          ? await post<Session>("/auth/login", { token })
          : await api<Session>("/auth/me");
        accept(s);
        setActivation(!!token || !s.email);
      } catch (e) {
        setSession(null);
        if (token || !(e instanceof ApiError && e.code === "UNAUTHORIZED"))
          setError(errorText(e));
      }
    })();
  }, []);
  useEffect(() => {
    const expired = () => {
      setSession(null);
      setCsrf("");
      setActivation(false);
    };
    window.addEventListener("campus:unauthorized", expired);
    return () => window.removeEventListener("campus:unauthorized", expired);
  }, []);
  if (session === undefined)
    return (
      <main className="login card" role="status">
        Loading your workspace…
      </main>
    );
  if (!session && route.pathname === "/")
    return (
      <>
        <Landing />
        {error && (
          <div className="landing-server-error">
            <ErrorBox message={error} />
          </div>
        )}
      </>
    );
  if (session && !activation && authRoute)
    return destination.startsWith("/admin/") ? (
      <ConsoleRedirect to={destination} />
    ) : (
      <Navigate to={destination} replace />
    );
  if (!session || activation)
    return (
      <main className="auth-layout">
        <section className="auth-story">
          <Link className="brand" to="/">
            <Logo />
          </Link>
          <h1>
            Big ideas.
            <br />
            <span>Shared power.</span>
          </h1>
          <p>
            Your projects, connected computers and results. All in one place.
          </p>
          <Link className="auth-back" to="/">
            ← Back to larpool
          </Link>
        </section>
        <section className="login card">
          <h2>
            {activation
              ? "Finish your account"
              : signup
                ? "Create your account"
                : "Sign in"}
          </h2>
          <p className="muted">
            {activation
              ? "Add an email and password for your next sign-in."
              : "Welcome to your shared computing workspace."}
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setError("");
              try {
                const s = await post<Session>(
                  activation
                    ? "/auth/account"
                    : signup
                      ? "/auth/register"
                      : "/auth/login",
                  signup || activation
                    ? { name: name.trim(), email: email.trim(), password }
                    : { email: email.trim(), password },
                );
                accept(s);
                setActivation(false);
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {(signup || activation) && (
              <label>
                Name
                <input
                  required
                  maxLength={256}
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            )}
            <label>
              Email
              <input
                type="email"
                required
                maxLength={254}
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                required
                minLength={signup || activation ? 12 : 1}
                maxLength={128}
                autoComplete={
                  signup || activation ? "new-password" : "current-password"
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {(signup || activation) && (
              <p className="muted">At least 12 characters.</p>
            )}
            <ErrorBox message={error} />
            <Button disabled={busy}>
              {busy
                ? "Please wait…"
                : activation
                  ? "Save account"
                  : signup
                    ? "Sign up"
                    : "Sign in"}
            </Button>
          </form>
          {!activation && (
            <p className="auth-switch">
              {signup ? "Already have an account? " : "New to larpool? "}
              <Link
                to={
                  (signup ? "/login" : "/register") +
                  "?next=" +
                  encodeURIComponent(destination)
                }
                onClick={() => setError("")}
              >
                {signup ? "Sign in" : "Create account"}
              </Link>
            </p>
          )}
        </section>
      </main>
    );
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/">
          <Logo />
        </Link>
        <nav aria-label="Main navigation">
          <NavLink to="/" end>
            <House aria-hidden="true" size={18} />
            Overview
          </NavLink>
          <NavLink to="/new">
            <Plus aria-hidden="true" size={18} />
            New task
          </NavLink>
          <NavLink to="/contribute">
            <Monitor aria-hidden="true" size={18} />
            Devices
          </NavLink>
          {session.role !== "member" && (
            <a href="/admin/">
              <ShieldCheck aria-hidden="true" size={18} />
              Pool console
            </a>
          )}
        </nav>
        <footer className="sidebar-account">
          <div className="account-avatar" aria-hidden="true">
            {session.name.trim().slice(0, 1).toUpperCase()}
          </div>
          <div className="account-name">
            <strong>{session.name}</strong>
            <span>Personal workspace</span>
          </div>
          <Button
            variant="ghost"
            aria-label="Sign out"
            onClick={async () => {
              try {
                await post("/auth/logout", {});
                Object.keys(sessionStorage)
                  .filter((k) => k.startsWith("campus-"))
                  .forEach((k) => sessionStorage.removeItem(k));
                setSession(null);
                setCsrf("");
                setPassword("");
                navigate("/login");
              } catch (e) {
                setError(errorText(e));
              }
            }}
          >
            <LogOut aria-hidden="true" size={18} />
          </Button>
        </footer>
      </aside>
      <div className="main">
        <main className="content" id="main">
          <ErrorBox message={error} />
          <Routes>
            <Route path="/" element={<Home ownerId={session.id} />} />
            <Route path="/new" element={<NewJob />} />
            <Route path="/contribute" element={<Contribute />} />
            <Route path="/jobs/:id" element={<JobDetails />} />
            <Route
              path="*"
              element={
                <p>
                  Page not found. <Link to="/">Back to workspace</Link>
                </p>
              }
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function ConsoleRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);
  return (
    <main className="login card" role="status">
      Opening pool console…
    </main>
  );
}
