import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import App from "./App";
import SessionListPage from "./pages/SessionListPage";
import SessionViewPage from "./pages/SessionViewPage";
import StatsPage from "./pages/StatsPage";
import BoardPage from "./pages/BoardPage";
import FollowPage from "./pages/FollowPage";
import AutopilotPage from "./pages/AutopilotPage";
import AlertsPage from "./pages/AlertsPage";
import InvestigationsPage from "./pages/InvestigationsPage";
import InvestigationView from "./pages/InvestigationView";
import JournalPage from "./pages/JournalPage";
import "./styles.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <SessionListPage /> },
      { path: "board", element: <BoardPage /> },
      { path: "follow", element: <FollowPage /> },
      { path: "autopilot", element: <AutopilotPage /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "investigations", element: <InvestigationsPage /> },
      { path: "investigations/:id", element: <InvestigationView /> },
      { path: "journal", element: <JournalPage /> },
      { path: "stats", element: <StatsPage /> },
      { path: "sessions/:sessionId", element: <SessionViewPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
