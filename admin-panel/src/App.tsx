import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import {
  Orbit,
  ArrowUpRight,
  LayoutDashboard,
  Monitor,
  Activity,
  ChartNoAxesCombined,
} from "lucide-react";
import { api, setCsrf, errorText } from "./lib/api";
import type { Session } from "./lib/types";
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
      setError("Your session expired. Sign in again through the portal.");
    };
    window.addEventListener("campus:unauthorized", expired);
    return () => window.removeEventListener("campus:unauthorized", expired);
  }, []);
  if (!session)
    return (
      <main className="login card">
        <h1>Pool console</h1>
        <p>
          Sign in through the <a href="/">user portal</a> to access your pool.
        </p>
        <ErrorBox message={error} />
      </main>
    );
  const admin = ["admin", "owner"].includes(session.role);
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" to="/">
          <Orbit strokeWidth={1.4} />
          Campus Pool
        </Link>
        <nav aria-label="Pool navigation">
          <p className="nav-label">WORKSPACE / OPERATIONS</p>
          <NavLink end to="/">
            <LayoutDashboard size={18} />
            Overview
          </NavLink>
          <NavLink to="/nodes">
            <Monitor size={18} />
            Devices
          </NavLink>
          <NavLink to="/jobs">
            <Activity size={18} />
            Jobs
          </NavLink>
          <NavLink to="/benchmarks">
            <ChartNoAxesCombined size={18} />
            Benchmarks
          </NavLink>
        </nav>
        <footer>
          POOL OPERATIONS
          <a href="/">
            User workspace <ArrowUpRight size={14} />
          </a>
        </footer>
      </aside>
      <div className="main">
        <div className="topbar">
          <span>
            Pool console / <b>Campus Pool</b>
          </span>
          <span className="pill">
            {session.name} · {session.role}
          </span>
        </div>
        <main className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/nodes" element={<Nodes admin={admin} />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/benchmarks" element={<Benchmarks admin={admin} />} />
            <Route path="*" element={<p>Page not found.</p>} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
