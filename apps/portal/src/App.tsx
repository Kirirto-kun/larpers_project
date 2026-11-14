import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import {
  Layers,
  House,
  Plus,
  Monitor,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { api, post, setCsrf, errorText, ApiError } from "./lib/api";
import type { Session } from "./lib/types";
import { Button } from "./components/ui/button";
import { ErrorBox } from "./components/Common";
import { Home } from "./pages/Home";
import { Contribute } from "./pages/Contribute";
import { NewJob } from "./pages/NewJob";
import { JobDetails } from "./pages/JobDetails";
export function App() {
  const [session, setSession] = useState<Session | null>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [signup, setSignup] = useState(false);
  const [activation, setActivation] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const started = useRef(false);
  function accept(s: Session) {
    setSignup(false);
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
        Подключаемся к Campus…
      </main>
    );
  if (!session || activation)
    return (
      <main className="auth-layout">
        <section className="auth-story">
          <Link className="brand" to="/">
            <Layers aria-hidden="true" />
            campus pool
          </Link>
          <h1>
            Больше возможностей.
            <br />
            Вместе.
          </h1>
          <p>
            Объединяйте свободные ресурсы компьютеров и превращайте задачи в
            результаты.
          </p>
          <span className="pill">Ваш компьютер. Ваш выбор.</span>
        </section>
        <section className="login card">
          <p className="eyebrow">ДОБРО ПОЖАЛОВАТЬ В CAMPUS</p>
          <h2>
            {activation
              ? "Завершите создание аккаунта"
              : signup
                ? "Создайте аккаунт"
                : "Вход в аккаунт"}
          </h2>
          <p className="muted">
            {activation
              ? "Укажите почту и пароль для следующих входов."
              : "Задачи, устройства и результаты в одном месте."}
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
                Имя
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
              Электронная почта
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
              Пароль
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
              <p className="muted">Не менее 12 символов.</p>
            )}
            <ErrorBox message={error} />
            <Button disabled={busy}>
              {busy
                ? "Подождите…"
                : activation
                  ? "Сохранить аккаунт"
                  : signup
                    ? "Зарегистрироваться"
                    : "Войти"}
            </Button>
          </form>
          {!activation && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setSignup(!signup);
                setError("");
              }}
            >
              {signup ? "Уже есть аккаунт? Войти" : "Создать аккаунт"}
            </Button>
          )}
        </section>
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
        <nav aria-label="Основная навигация">
          <NavLink to="/" end>
            <House aria-hidden="true" size={18} />
            Обзор
          </NavLink>
          <NavLink to="/new">
            <Plus aria-hidden="true" size={18} />
            Новая задача
          </NavLink>
          <NavLink to="/contribute">
            <Monitor aria-hidden="true" size={18} />
            Устройства
          </NavLink>
          {session.role !== "member" && (
            <a href="/admin/">
              <ShieldCheck aria-hidden="true" size={18} />
              Управление пулом
            </a>
          )}
        </nav>
        <footer>
          ОБЩИЕ РЕСУРСЫ. ВАШИ ИДЕИ.<p>Участвуйте на своих условиях.</p>
        </footer>
      </aside>
      <div className="main">
        <div className="topbar">
          <span>Рабочее пространство</span>
          <div className="actions" style={{ margin: 0 }}>
            <span>{session.name}</span>
            <Button
              variant="ghost"
              aria-label="Выйти"
              onClick={async () => {
                try {
                  await post("/auth/logout", {});
                  Object.keys(sessionStorage)
                    .filter((k) => k.startsWith("campus-"))
                    .forEach((k) => sessionStorage.removeItem(k));
                  setSession(null);
                  setCsrf("");
                  setPassword("");
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              <LogOut aria-hidden="true" size={18} />
            </Button>
          </div>
        </div>
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
                  Страница не найдена. <Link to="/">На главную</Link>
                </p>
              }
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
