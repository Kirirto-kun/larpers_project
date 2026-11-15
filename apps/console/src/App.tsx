import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import {
  LayoutDashboard,
  Monitor,
  Activity,
  ChartNoAxesCombined,
} from "lucide-react";
import { api, setCsrf, errorText } from "./lib/api";
import type { Session } from "./lib/types";
import { Logo } from "./components/Logo";
import { ErrorBox } from "./components/Common";
import { Overview } from "./pages/Overview";
import { Nodes } from "./pages/Nodes";
import { Jobs } from "./pages/Jobs";
import { Benchmarks } from "./pages/Benchmarks";
export function App() {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState("");
  useEffect(() => {
    api<Session>("/auth/me")
      .then((s) => {
        setSession(s);
        setCsrf(s.csrf_token || "");
      })
      .catch((e) => setError(errorText(e)));
  }, []);
  useEffect(() => {
    const expired = () => {
      setSession(undefined);
      setCsrf("");
      setError("Your session expired. Sign in again.");
    };
    window.addEventListener("campus:unauthorized", expired);
    return () => window.removeEventListener("campus:unauthorized", expired);
  }, []);
  if (!session)
    return (
      <main className="login card">
        <h1>Pool console</h1>
        <p>
          <a
            className="button primary"
            href={"/login?next=" + encodeURIComponent(location.pathname)}
          >
            Sign in
          </a>
        </p>
        <ErrorBox message={error} />
      </main>
    );
  if (session.role === "member")
    return (
      <main className="login card">
        <h1>Admin access required</h1>
        <p>This area is available to pool owners and administrators.</p>
        <a className="button primary" href="/">
          Back to workspace
        </a>
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
        <nav aria-label="Pool navigation">
          <NavLink end to="/">
            <LayoutDashboard aria-hidden="true" size={18} />
            Overview
          </NavLink>
          <NavLink to="/nodes">
            <Monitor aria-hidden="true" size={18} />
            Devices
          </NavLink>
          <NavLink to="/jobs">
            <Activity aria-hidden="true" size={18} />
            Tasks
          </NavLink>
          <NavLink to="/benchmarks">
            <ChartNoAxesCombined aria-hidden="true" size={18} />
            Benchmarks
          </NavLink>
        </nav>
        <footer>
          <a className="sidebar-workspace" href="/">
            ← Workspace
          </a>
          <p className="sidebar-identity">{session.name}</p>
        </footer>
      </aside>
      <div className="main">
        <main className="content" id="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/nodes" element={<Nodes />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/benchmarks" element={<Benchmarks />} />
            <Route path="*" element={<p>Page not found.</p>} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
