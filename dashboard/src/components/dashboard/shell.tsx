"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import {
  LayoutDashboard,
  ClipboardCheck,
  History,
  Server,
  Menu,
  Shield,
} from "lucide-react";

export type ViewKey = "summary" | "approvals" | "history" | "system";

interface NavItem {
  key: ViewKey;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  description: string;
}

const NAV_ITEMS: NavItem[] = [
  {
    key: "summary",
    label: "Resumen",
    icon: LayoutDashboard,
    description: "Estado del agente y actividad reciente",
  },
  {
    key: "approvals",
    label: "Aprobaciones",
    icon: ClipboardCheck,
    description: "Solicitudes pendientes de decisión humana",
  },
  {
    key: "history",
    label: "Historial",
    icon: History,
    description: "Ejecuciones y decisiones pasadas",
  },
  {
    key: "system",
    label: "Sistema",
    icon: Server,
    description: "Estado de API, persistencia y backups",
  },
];

interface NavListProps {
  activeView: ViewKey;
  onViewChange: (view: ViewKey) => void;
  onNavigate?: () => void;
}

function NavList({ activeView, onViewChange, onNavigate }: NavListProps) {
  return (
    <nav className="flex flex-col gap-1 p-3" aria-label="Navegación principal">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = activeView === item.key;
        return (
          <Button
            key={item.key}
            variant={isActive ? "secondary" : "ghost"}
            className={cn(
              "justify-start h-auto py-2.5 px-3 font-normal",
              isActive && "bg-secondary",
            )}
            onClick={() => {
              onViewChange(item.key);
              onNavigate?.();
            }}
            aria-current={isActive ? "page" : undefined}
          >
            <Icon className="mr-2.5 h-4 w-4 flex-shrink-0" />
            <span className="flex flex-col items-start">
              <span className="text-sm font-medium leading-tight">
                {item.label}
              </span>
              <span className="text-xs text-muted-foreground leading-tight hidden sm:inline">
                {item.description}
              </span>
            </span>
          </Button>
        );
      })}
    </nav>
  );
}

interface DashboardShellProps {
  activeView: ViewKey;
  onViewChange: (view: ViewKey) => void;
  isDemoMode: boolean;
  children: React.ReactNode;
}

export function DashboardShell({
  activeView,
  onViewChange,
  isDemoMode,
  children,
}: DashboardShellProps) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const activeItem = NAV_ITEMS.find((n) => n.key === activeView);

  return (
    <div className="min-h-screen flex flex-col bg-muted/30">
      <header className="flex items-center justify-between border-b bg-background px-4 py-3 sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                aria-label="Abrir navegación"
              >
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <div className="flex items-center gap-2 border-b px-4 py-3">
                <Shield className="h-5 w-5 text-foreground" />
                <span className="font-semibold text-sm">AGENTE GENERAL</span>
              </div>
              <NavList
                activeView={activeView}
                onViewChange={onViewChange}
                onNavigate={() => setMobileNavOpen(false)}
              />
            </SheetContent>
          </Sheet>

          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-foreground" />
            <span className="font-semibold text-sm hidden sm:inline">
              AGENTE GENERAL
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isDemoMode && (
            <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
              DEMO
            </span>
          )}
          <span className="text-xs text-muted-foreground hidden sm:inline">
            HITL supervision
          </span>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="hidden md:flex md:w-64 md:flex-col md:border-r bg-background">
          <div className="flex items-center gap-2 border-b px-4 py-3">
            <Shield className="h-4 w-4 text-muted-foreground" />
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Navegación
            </span>
          </div>
          <NavList activeView={activeView} onViewChange={onViewChange} />
        </aside>

        <main className="flex-1 overflow-x-hidden">
          <div className="border-b bg-background px-4 py-3 sm:px-6 sm:py-4">
            <h1 className="text-lg font-semibold sm:text-xl">
              {activeItem?.label}
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5 sm:text-sm">
              {activeItem?.description}
            </p>
          </div>
          <div className="p-4 sm:p-6">{children}</div>
        </main>
      </div>

      <footer className="border-t bg-background px-4 py-3 mt-auto">
        <p className="text-xs text-muted-foreground text-center">
          AGENTE GENERAL — Dashboard de supervisión HITL. El backend es la
          autoridad para autenticación, autorización y decisiones. No se
          almacenan tokens ni secretos en el frontend.
        </p>
      </footer>
    </div>
  );
}
