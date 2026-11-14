import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import {
  Layers,
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
      setError("Сессия завершена. Войдите снова через портал.");
    };
    window.addEventListener("campus:unauthorized", expired);
    return () => window.removeEventListener("campus:unauthorized", expired);
  }, []);
  if (!session)
    return (
      <main className="login card">
        <h1>Управление Campus</h1>
        <p>
          Войдите через <a href="/">пользовательский портал</a>.
        </p>
        <ErrorBox message={error} />
      </main>
    );
  if (session.role === "member")
    return (
      <main className="login card">
        <h1>Доступ для администратора</h1>
        <p>Управление пулом доступно владельцу и администраторам.</p>
        <a className="button primary" href="/">
          К своим задачам и устройствам
        </a>
      </main>
    );
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        К содержимому
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/">
          <Layers aria-hidden="true" />
          campus pool
        </Link>
        <nav aria-label="Управление пулом">
          <NavLink end to="/">
            <LayoutDashboard aria-hidden="true" size={18} />
            Обзор пула
          </NavLink>
          <NavLink to="/nodes">
            <Monitor aria-hidden="true" size={18} />
            Устройства
          </NavLink>
          <NavLink to="/jobs">
            <Activity aria-hidden="true" size={18} />
            Задачи
          </NavLink>
          <NavLink to="/benchmarks">
            <ChartNoAxesCombined aria-hidden="true" size={18} />
            Измерения
          </NavLink>
        </nav>
        <footer>
          УПРАВЛЕНИЕ CAMPUS
          <p>
            <a href="/">← Рабочее пространство</a>
          </p>
        </footer>
      </aside>
      <div className="main">
        <div className="topbar">
          <span>Управление пулом</span>
          <span className="pill">
            {session.name} ·{" "}
            {session.role === "owner" ? "владелец" : "администратор"}
          </span>
        </div>
        <main className="content" id="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/nodes" element={<Nodes />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/benchmarks" element={<Benchmarks />} />
            <Route path="*" element={<p>Страница не найдена.</p>} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
