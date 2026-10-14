"use client";

import { useState } from "react";
import { DashboardShell, type ViewKey } from "@/components/dashboard/shell";
import { SummaryView } from "@/components/dashboard/summary-view";
import { ApprovalsView } from "@/components/dashboard/approvals-view";
import { HistoryView } from "@/components/dashboard/history-view";
import { SystemView } from "@/components/dashboard/system-view";

export default function Home() {
  const [activeView, setActiveView] = useState<ViewKey>("summary");

  // Demo mode is determined by whether the backend is reachable.
  // The shell shows a "DEMO" badge when any fetch returns is_demo=true.
  // We start with true until the first successful real fetch.
  const [isDemoMode, setIsDemoMode] = useState(true);

  // The shell doesn't fetch directly — each view fetches its own data
  // and reports demo state via a callback. For simplicity, we assume
  // demo mode is on until the first successful real API call.
  // A more robust approach would use a context, but this keeps the
  // code minimal and the dependency direction clear (views → shell).

  return (
    <DashboardShell
      activeView={activeView}
      onViewChange={setActiveView}
      isDemoMode={isDemoMode}
    >
      {activeView === "summary" && (
        <SummaryView onNavigateToApprovals={() => setActiveView("approvals")} />
      )}
      {activeView === "approvals" && <ApprovalsView />}
      {activeView === "history" && <HistoryView />}
      {activeView === "system" && <SystemView />}
    </DashboardShell>
  );
}
