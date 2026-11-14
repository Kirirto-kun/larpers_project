import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { Layers, House, Plus, Monitor, LogOut } from "lucide-react";
import { api, post, setCsrf, errorText } from "./lib/api";
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
  const [token, setToken] = useState("");
  useEffect(() => {
    api<Session>("/auth/me")
      .then((s) => {
        setSession(s);
        setCsrf(s.csrf_token || "");
      })
      .catch((e) => {
        setSession(null);
        if (e.code !== "401" && e.code !== "UNAUTHORIZED")
          setError(errorText(e));
      });
  }, []);
  if (session === undefined)
    return <main className="login">Connecting to Campus Pool…</main>;
  if (!session)
    return (
      <main className="login card">
        <h1>Welcome to Campus Pool</h1>
        <p>Use your member invitation to join your team's private pool.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const s = await post<Session>("/auth/login", {
                invitation: token,
              });
              setSession(s);
              setCsrf(s.csrf_token || "");
              setError("");
            } catch (e) {
              setError(errorText(e));
            }
          }}
        >
          <label>
            Member invitation
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              type="password"
              autoComplete="off"
            />
          </label>
          <Button>Sign in</Button>
          <ErrorBox message={error} />
        </form>
      </main>
    );
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" to="/">
          <Layers />
          campus pool
        </Link>
        <nav>
          <NavLink to="/" end>
            <House size={18} />
            Workspace
          </NavLink>
          <NavLink to="/new">
            <Plus size={18} />
            Run a task
          </NavLink>
          <NavLink to="/contribute">
            <Monitor size={18} />
            Contribute compute
          </NavLink>
        </nav>
        <footer>
          PRIVATE COMPUTE, SHARED POSSIBILITY
          <br />
          <br />
          Your team's devices. Your data.
        </footer>
      </aside>
      <div className="main">
        <div className="topbar">
          <span>
            Workspace / <b>Campus Pool</b>
          </span>
          <div className="actions" style={{ margin: 0 }}>
            <span>{session.name}</span>
            <Button
              variant="ghost"
              aria-label="Sign out"
              onClick={async () => {
                try {
                  await post("/auth/logout", {});
                  setSession(null);
                  setCsrf("");
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              <LogOut size={16} />
            </Button>
          </div>
        </div>
        <main className="content">
          <ErrorBox message={error} />
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewJob />} />
            <Route path="/contribute" element={<Contribute />} />
            <Route path="/jobs/:id" element={<JobDetails />} />
            <Route
              path="*"
              element={
                <p>
                  Page not found. <Link to="/">Return to workspace</Link>
                </p>
              }
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
