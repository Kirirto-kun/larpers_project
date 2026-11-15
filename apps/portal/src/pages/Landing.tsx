import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowUpRight,
  Check,
  Cpu,
  Menu,
  Monitor,
  Pause,
  ShieldCheck,
  X,
} from "lucide-react";
import { Logo } from "../components/Logo";
import { ParticleGlobe } from "../components/ParticleGlobe";
import "./landing.css";

const links = [
  ["Platform", "#platform"],
  ["How it works", "#how-it-works"],
  ["Contribute", "#contribute"],
  ["Questions", "#questions"],
];
const questions = [
  [
    "What can I run on larpool?",
    "Upload your code and data, or start with a ready-made example. Review the execution plan before starting your task.",
  ],
  [
    "Do I need to contribute a computer?",
    "You can submit tasks, contribute a computer, or do both. Tasks run when compatible devices are available in your pool.",
  ],
  [
    "Can I pause my contribution?",
    "Yes. Pause your device from your workspace at any time.",
  ],
  [
    "What does the AI receive?",
    "Only the task description, file information and project text you explicitly choose to share. You review this before preparing a plan.",
  ],
];

export function Landing() {
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  return (
    <div className="landing" id="top">
      <a className="skip-link" href="#landing-main">
        Skip to content
      </a>
      <header className="landing-header">
        <div className="landing-container landing-nav">
          <Link className="brand" to="/" aria-label="larpool home">
            <Logo />
          </Link>
          <nav className="landing-desktop-nav" aria-label="Main navigation">
            {links.map(([label, href]) => (
              <a key={href} href={href}>
                {label}
              </a>
            ))}
          </nav>
          <div className="landing-nav-actions">
            <Link className="landing-signin" to="/login">
              Sign in <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
            <button
              className="landing-menu-button"
              aria-label={menu ? "Close menu" : "Open menu"}
              aria-expanded={menu}
              aria-controls="landing-mobile-nav"
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
            </button>
          </div>
        </div>
        {menu && (
          <nav
            id="landing-mobile-nav"
            className="landing-mobile-nav"
            aria-label="Page sections"
          >
            {links.map(([label, href]) => (
              <a key={href} href={href} onClick={() => setMenu(false)}>
                {label}
                <ArrowUpRight size={16} aria-hidden="true" />
              </a>
            ))}
          </nav>
        )}
      </header>
      <main id="landing-main">
        <section className="landing-hero landing-container">
          <div className="landing-hero-copy">
            <p className="landing-kicker">
              <span /> A shared computing platform
            </p>
            <h1>
              Big ideas.
              <br />
              <span>Shared power.</span>
            </h1>
            <p className="landing-lead">
              Turn the computers around you into a computing pool. Run your
              projects, or put your spare capacity to work.
            </p>
            <div className="landing-actions">
              <Link className="landing-button" to="/register?next=%2Fnew">
                Run a task <ArrowUpRight size={19} aria-hidden="true" />
              </Link>
              <Link
                className="landing-button landing-button-outline"
                to="/register?next=%2Fcontribute"
              >
                Connect a computer <ArrowUpRight size={19} aria-hidden="true" />
              </Link>
            </div>
            <div className="landing-hero-note">
              <ShieldCheck size={16} aria-hidden="true" /> Your tasks. Your
              resources. Your control.
            </div>
          </div>
          <div className="landing-visual">
            <ParticleGlobe />
            <span className="globe-label globe-label-top">
              <span className="mint-dot" /> Your project
            </span>
            <span className="globe-label globe-label-bottom">
              <Check size={14} aria-hidden="true" /> Results, delivered
            </span>
            <div className="globe-caption">
              <span className="mint-dot" /> One pool. More possibilities.
            </div>
          </div>
          <a className="landing-scroll" href="#platform">
            <ArrowDown size={16} aria-hidden="true" /> Explore the platform
          </a>
        </section>
        <div className="landing-capabilities">
          <div className="landing-container">
            <span>Built for your next project</span>
            <span>CPU & GPU</span>
            <span>Code & data</span>
            <span>Shared resources</span>
            <span>Downloadable results</span>
          </div>
        </div>
        <section className="landing-light" id="platform">
          <div className="landing-container landing-section">
            <div className="landing-section-heading">
              <p className="landing-kicker">The platform</p>
              <h2>
                One pool.
                <br />
                <span>Two ways to take part.</span>
              </h2>
            </div>
            <div className="landing-feature-grid">
              <article className="landing-feature">
                <div className="landing-feature-icon">
                  <Cpu size={25} aria-hidden="true" />
                </div>
                <span className="landing-index">01 / RUN</span>
                <h3>From project to result.</h3>
                <p>
                  Add your code and data. Review a plan, follow the progress and
                  download the output, all in one workspace.
                </p>
                <Link to="/register?next=%2Fnew">
                  Create your first task{" "}
                  <ArrowUpRight size={18} aria-hidden="true" />
                </Link>
              </article>
              <article className="landing-feature">
                <div className="landing-feature-icon">
                  <Monitor size={25} aria-hidden="true" />
                </div>
                <span className="landing-index">02 / CONTRIBUTE</span>
                <h3>Make spare power useful.</h3>
                <p>
                  Connect a computer and choose how much to share. Keep working
                  as usual, and pause your contribution whenever you need to.
                </p>
                <Link to="/register?next=%2Fcontribute">
                  Connect your computer{" "}
                  <ArrowUpRight size={18} aria-hidden="true" />
                </Link>
              </article>
            </div>
          </div>
        </section>
        <section className="landing-light landing-how" id="how-it-works">
          <div className="landing-container landing-section">
            <div className="landing-section-heading">
              <p className="landing-kicker">How it works</p>
              <h2>
                Less setup.
                <br />
                <span>More getting things done.</span>
              </h2>
            </div>
            <div className="landing-steps">
              {[
                [
                  "01",
                  "Bring your project",
                  "Describe your task and add the files it needs, or explore a ready-made example.",
                ],
                [
                  "02",
                  "Review. Then run.",
                  "Check the plan before you start. larpool sends the work to compatible devices.",
                ],
                [
                  "03",
                  "Take your results",
                  "Follow your task in the workspace and download the completed output.",
                ],
              ].map(([n, title, text]) => (
                <article key={n}>
                  <span>{n}</span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
        <section className="landing-contribute" id="contribute">
          <div className="landing-container landing-section landing-contribute-grid">
            <div>
              <p className="landing-kicker">On your terms</p>
              <h2>
                Your computer.
                <br />
                <span>Your call.</span>
              </h2>
              <p className="landing-lead">
                A little capacity can go a long way. Set the pace that works for
                you.
              </p>
              <Link
                className="landing-button"
                to="/register?next=%2Fcontribute"
              >
                Start contributing <ArrowUpRight size={19} aria-hidden="true" />
              </Link>
            </div>
            <div className="landing-control-preview">
              <div className="preview-device">
                <Monitor size={25} aria-hidden="true" />
                <div>
                  <strong>Your computer</strong>
                  <span>You choose what to share</span>
                </div>
                <span className="preview-label">PREVIEW</span>
              </div>
              <div className="preview-modes">
                {[
                  ["10%", "Light"],
                  ["50%", "Balanced"],
                  ["90%", "Maximum"],
                ].map(([n, label], i) => (
                  <div key={n} className={i === 1 ? "selected" : ""}>
                    <strong>{n}</strong>
                    <span>{label}</span>
                    {i === 1 && <Check size={16} aria-hidden="true" />}
                  </div>
                ))}
              </div>
              <div className="preview-footer">
                <Pause size={16} aria-hidden="true" />
                <span>Pause whenever you need to.</span>
              </div>
            </div>
          </div>
        </section>
        <section className="landing-light" id="questions">
          <div className="landing-container landing-section landing-faq-grid">
            <div className="landing-section-heading">
              <p className="landing-kicker">Good to know</p>
              <h2>
                A few
                <br />
                <span>questions.</span>
              </h2>
            </div>
            <div className="landing-faq">
              {questions.map(([q, a]) => (
                <details key={q}>
                  <summary>
                    {q}
                    <span aria-hidden="true">+</span>
                  </summary>
                  <p>{a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>
        <section className="landing-final">
          <div className="landing-container">
            <p className="landing-kicker">Start something</p>
            <h2>
              Your next idea.
              <br />
              <span>A little more power.</span>
            </h2>
            <Link className="landing-button" to="/register">
              Create an account <ArrowUpRight size={19} aria-hidden="true" />
            </Link>
            <p>
              Already part of the pool? <Link to="/login">Sign in</Link>
            </p>
          </div>
        </section>
      </main>
      <footer className="landing-footer landing-container">
        <Link className="brand" to="/" aria-label="larpool home">
          <Logo />
        </Link>
        <span>© {new Date().getFullYear()} larpool</span>
        <a href="#top">
          Back to top <ArrowUpRight size={15} aria-hidden="true" />
        </a>
      </footer>
    </div>
  );
}
