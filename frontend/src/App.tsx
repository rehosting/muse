import { NavLink, Outlet } from "react-router-dom";
import CommandPalette from "./components/CommandPalette";
import LaunchModal from "./components/LaunchModal";
import LoginGate from "./components/LoginGate";
import ThemeToggle from "./components/ThemeToggle";
import { useKeyboardInset } from "./hooks/useKeyboardInset";

export default function App() {
  useKeyboardInset(); // publishes --kb-inset so mobile composers ride above the keyboard
  return (
    <div className="app">
      <nav className="navbar">
        <NavLink to="/" className="nav-brand">
          muse
        </NavLink>
        <NavLink to="/" end className="nav-link">
          Sessions
        </NavLink>
        <NavLink to="/board" className="nav-link">
          Monitor
        </NavLink>
        <NavLink to="/panes" className="nav-link">
          Panes
        </NavLink>
        <NavLink to="/autopilot" className="nav-link">
          Autopilot
        </NavLink>
        <NavLink to="/alerts" className="nav-link">
          Alerts
        </NavLink>
        <NavLink to="/investigations" className="nav-link">
          Investigations
        </NavLink>
        <NavLink to="/journal" className="nav-link">
          Journal
        </NavLink>
        <NavLink to="/files" className="nav-link">
          Files
        </NavLink>
        <NavLink to="/ask" className="nav-link">
          Ask
        </NavLink>
        <NavLink to="/stats" className="nav-link">
          Stats
        </NavLink>
        <NavLink to="/insights" className="nav-link">
          Insights
        </NavLink>
        <button
          className="nav-search"
          onClick={() => window.dispatchEvent(new Event("muse:search"))}
          title="Search all sessions (⌘K)"
        >
          Search <kbd>⌘K</kbd>
        </button>
        <ThemeToggle />
      </nav>
      <main className="content">
        <Outlet />
      </main>
      <CommandPalette />
      <LaunchModal />
      <LoginGate />
    </div>
  );
}
