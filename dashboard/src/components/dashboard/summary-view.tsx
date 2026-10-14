"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  RefreshCw,
  XCircle,
  Pause,
} from "lucide-react";
import {
  fetchSystemStatus,
  fetchPendingApprovals,
  type SystemStatus,
} from "@/lib/api";

interface SummaryViewProps {
  onNavigateToApprovals: () => void;
}

export function SummaryView({ onNavigateToApprovals }: SummaryViewProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [pendingCount, setPendingCount] = useState<number>(0);
  const [approvalsAvailable, setApprovalsAvailable] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    const [sysStatus, approvals] = await Promise.all([
      fetchSystemStatus(),
      fetchPendingApprovals(),
    ]);
    setStatus(sysStatus);
    setPendingCount(approvals.approvals.length);
    setApprovalsAvailable(approvals.available);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setRefreshing(true);
      const [sysStatus, approvals] = await Promise.all([
        fetchSystemStatus(),
        fetchPendingApprovals(),
      ]);
      if (cancelled) return;
      setStatus(sysStatus);
      setPendingCount(approvals.approvals.length);
      setApprovalsAvailable(approvals.available);
      setLoading(false);
      setRefreshing(false);
    };
    run();
    const interval = setInterval(run, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loading && !status) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-28 w-full" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );
  }

  if (!status) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Error al cargar el estado</AlertTitle>
        <AlertDescription>
          No se pudo obtener información del backend. Verifica la conexión y
          vuelve a intentarlo.
        </AlertDescription>
      </Alert>
    );
  }

  const agentStatus = deriveAgentStatus(status);
  const lastError = status.health?.fetch_error || status.api_error;

  return (
    <div className="grid gap-4">
      {/* Agent status banner */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <StatusIcon status={agentStatus} />
              <div>
                <CardTitle className="text-base">
                  Estado del agente: {statusLabel(agentStatus)}
                </CardTitle>
                <CardDescription className="text-xs">
                  {status.agent_info?.name || "albra-seed"} ·{" "}
                  {status.agent_info?.version || "1.0.0"}
                </CardDescription>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={load}
              disabled={refreshing}
              aria-label="Actualizar estado"
            >
              <RefreshCw
                className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
              />
              <span className="ml-1 hidden sm:inline">Actualizar</span>
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3 w-3" />
              Última actualización:{" "}
              {new Date(status.last_updated).toLocaleString("es-ES", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                day: "2-digit",
                month: "2-digit",
              })}
            </span>
            {status.is_demo && (
              <Badge variant="outline" className="text-amber-700 border-amber-500/30">
                Datos simulados
              </Badge>
            )}
            {!status.api_reachable && (
              <Badge variant="outline" className="text-red-700 border-red-500/30">
                API no alcanzable
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Pending approvals alert */}
      {pendingCount > 0 && approvalsAvailable && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{pendingCount} aprobación(es) pendiente(s)</AlertTitle>
          <AlertDescription className="flex items-center justify-between">
            <span>
              Hay solicitudes esperando decisión humana. El agente no puede
              proceder sin aprobación.
            </span>
            <Button size="sm" variant="outline" onClick={onNavigateToApprovals}>
              Revisar
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {!approvalsAvailable && (
        <Alert variant="default">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Aprobaciones no disponibles</AlertTitle>
          <AlertDescription>
            El backend no expone rutas de aprobaciones. Integración pendiente.
            La vista "Aprobaciones" muestra el estado de la integración.
          </AlertDescription>
        </Alert>
      )}

      {/* Last error */}
      {lastError && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>Último error conocido</AlertTitle>
          <AlertDescription className="font-mono text-xs">
            {lastError}
          </AlertDescription>
        </Alert>
      )}

      {/* Stats grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1.5">
              <ClipboardCheckIcon />
              Aprobaciones pendientes
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold">
                {approvalsAvailable ? pendingCount : "—"}
              </span>
              {!approvalsAvailable && (
                <span className="text-xs text-muted-foreground">
                  no disponible
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {approvalsAvailable
                ? pendingCount === 0
                  ? "Sin solicitudes pendientes"
                  : "Requieren decisión humana"
                : "Backend no implementa el endpoint"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1.5">
              <Activity className="h-3 w-3" />
              Fase del runtime
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold capitalize">
                {status.health?.phase || status.readiness?.phase || "unknown"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Sesiones activas: {status.health?.sessions ?? 0} · Uptime:{" "}
              {formatUptime(status.health?.uptime_seconds ?? 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3 w-3" />
              Readiness
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold">
                {status.readiness?.ready ? "Listo" : "No listo"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {status.readiness?.ready
                ? "El runtime acepta tráfico"
                : "El runtime no acepta tráfico"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Components status */}
      {status.health?.components &&
        Object.keys(status.health.components).length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Componentes del runtime</CardTitle>
              <CardDescription>
                Estado individual de cada componente registrado
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(status.health.components).map(([name, state]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded-md border px-3 py-2"
                  >
                    <span className="text-sm font-mono">{name}</span>
                    <ComponentBadge state={String(state)} />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
    </div>
  );
}

// Helpers

function ClipboardCheckIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3 w-3"
    >
      <rect width="8" height="4" x="8" y="2" rx="1" ry="1" />
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <path d="m9 14 2 2 4-4" />
    </svg>
  );
}

type AgentStatus = "stopped" | "active" | "degraded" | "requires_attention";

function deriveAgentStatus(s: SystemStatus): AgentStatus {
  if (!s.api_reachable) return "stopped";
  if (s.readiness?.ready === false) return "degraded";
  const pendingApprovals =
    s.health?.components &&
    Object.values(s.health.components).some(
      (v) => String(v) === "not_configured",
    );
  if (pendingApprovals) return "degraded";
  if (s.health?.status === "healthy" && s.readiness?.ready) return "active";
  return "requires_attention";
}

function statusLabel(s: AgentStatus): string {
  const labels: Record<AgentStatus, string> = {
    stopped: "Detenido",
    active: "Activo",
    degraded: "Degradado",
    requires_attention: "Requiere atención",
  };
  return labels[s];
}

function StatusIcon({ status }: { status: AgentStatus }) {
  const className = "h-5 w-5";
  switch (status) {
    case "active":
      return <CheckCircle2 className={`${className} text-emerald-600`} />;
    case "degraded":
      return <AlertTriangle className={`${className} text-amber-600`} />;
    case "requires_attention":
      return <AlertTriangle className={`${className} text-orange-600`} />;
    case "stopped":
    default:
      return <Pause className={`${className} text-muted-foreground`} />;
  }
}

function ComponentBadge({ state }: { state: string }) {
  const variant =
    state === "healthy" || state === "ok" || state === "ready"
      ? "default"
      : state === "degraded" || state === "warning"
        ? "secondary"
        : "outline";
  const className =
    variant === "default"
      ? "bg-emerald-500/10 text-emerald-700 border-emerald-500/30"
      : variant === "secondary"
        ? "bg-amber-500/10 text-amber-700 border-amber-500/30"
        : "text-muted-foreground";
  return (
    <Badge variant={variant} className={`text-xs ${className}`}>
      {state}
    </Badge>
  );
}

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}
