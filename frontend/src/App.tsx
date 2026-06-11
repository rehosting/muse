import { NavLink, Outlet } from "react-router-dom";
import CommandPalette from "./components/CommandPalette";
import ThemeToggle from "./components/ThemeToggle";

export default function App() {
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
        <NavLink to="/stats" className="nav-link">
          Stats
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
    </div>
  );
}
