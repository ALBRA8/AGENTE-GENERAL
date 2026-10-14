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
  Server,
  Database,
  HardDrive,
  ShieldCheck,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
} from "lucide-react";
import { fetchSystemStatus, type SystemStatus } from "@/lib/api";

export function SystemView() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    const s = await fetchSystemStatus();
    setStatus(s);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setRefreshing(true);
      const s = await fetchSystemStatus();
      if (cancelled) return;
      setStatus(s);
      setLoading(false);
      setRefreshing(false);
    };
    run();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading && !status) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!status) {
    return (
      <Alert variant="destructive">
        <XCircle className="h-4 w-4" />
        <AlertTitle>Error al cargar el estado del sistema</AlertTitle>
      </Alert>
    );
  }

  return (
    <div className="grid gap-4">
      {/* API status */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="h-4 w-4" />
            API y conexión
          </CardTitle>
          <CardDescription>
            Estado de la conexión con el backend AGENTE GENERAL.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <StatusRow
            label="API alcanzable"
            status={status.api_reachable ? "ok" : "error"}
            detail={
              status.api_reachable
                ? "El backend responde a las peticiones"
                : status.api_error || "No se pudo conectar con el backend"
            }
          />
          <StatusRow
            label="Endpoint /health"
            status={
              status.health && !status.health.fetch_error ? "ok" : "error"
            }
            detail={
              status.health?.fetch_error
                ? status.health.fetch_error
                : status.health
                  ? `status=${status.health.status}, phase=${status.health.phase}`
                  : "No disponible"
            }
          />
          <StatusRow
            label="Endpoint /readiness"
            status={
              status.readiness && !status.readiness.fetch_error ? "ok" : "error"
            }
            detail={
              status.readiness?.fetch_error
                ? status.readiness.fetch_error
                : status.readiness
                  ? `ready=${status.readiness.ready}, phase=${status.readiness.phase}`
                  : "No disponible"
            }
          />
          <StatusRow
            label="Endpoint /agent/info"
            status={
              status.agent_info && !status.agent_info.fetch_error
                ? "ok"
                : "error"
            }
            detail={
              status.agent_info?.fetch_error
                ? status.agent_info.fetch_error
                : status.agent_info
                  ? `agent_id=${status.agent_info.agent_id}, name=${status.agent_info.name}`
                  : "No disponible"
            }
          />
          {status.is_demo && (
            <Alert variant="default" className="mt-2">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription className="text-xs">
                Los datos anteriores son simulados porque el backend no está
                disponible. Cuando se restaure la conexión, los datos reales
                reemplazarán automáticamente los simulados.
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Persistence status */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Database className="h-4 w-4" />
            Persistencia
          </CardTitle>
          <CardDescription>
            Estado del subsistema de persistencia (si el backend lo expone).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <StatusRow
            label="Estado de persistencia"
            status={
              status.persistence_status === "available"
                ? "ok"
                : status.persistence_status === "unavailable"
                  ? "error"
                  : "unknown"
            }
            detail={
              status.persistence_status === "available"
                ? "Disponible y operativa"
                : status.persistence_status === "unavailable"
                  ? "No disponible"
                  : "El backend no expone estado de persistencia — no se puede verificar"
            }
          />
          <p className="text-xs text-muted-foreground mt-2">
            El backend actual no expone un endpoint de estado de persistencia.
            No afirmamos que esté operativa sin evidencia.
          </p>
        </CardContent>
      </Card>

      {/* Backup status */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <HardDrive className="h-4 w-4" />
            Copia de seguridad
          </CardTitle>
          <CardDescription>
            Última copia de seguridad verificada — solo si hay evidencia real.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <StatusRow
            label="Último backup verificado"
            status={
              status.last_backup_verified === "verified"
                ? "ok"
                : status.last_backup_verified === "not_verified"
                  ? "error"
                  : "unknown"
            }
            detail={
              status.last_backup_verified === "verified"
                ? "Verificado"
                : status.last_backup_verified === "not_verified"
                  ? "No verificado"
                  : "El backend no proporciona información sobre backups. No afirmamos que existan sin evidencia."
            }
          />
          <p className="text-xs text-muted-foreground mt-2">
            No se afirma que los backups existen ni que la recuperación está
            verificada sin datos proporcionados por el backend.
          </p>
        </CardContent>
      </Card>

      {/* Security controls */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            Controles de seguridad
          </CardTitle>
          <CardDescription>
            Resumen de los controles de seguridad observados.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="rounded-md border p-3">
            <p className="text-xs font-medium mb-1">
              Autenticación y autorización
            </p>
            <p className="text-xs text-muted-foreground">
              El backend es la autoridad. El dashboard no almacena tokens ni
              secretos. Las cookies de sesión son httpOnly, Secure,
              SameSite=Strict, gestionadas exclusivamente por el backend.
            </p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs font-medium mb-1">Política por defecto</p>
            <p className="text-xs text-muted-foreground">
              Denegar por defecto cuando no se pueda verificar la autorización.
              Ninguna acción procede sin aprobación explícita del backend.
            </p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs font-medium mb-1">Reintentos</p>
            <p className="text-xs text-muted-foreground">
              No se reintentan automáticamente acciones externas de resultado
              incierto. El usuario debe confirmar manualmente cualquier reintento.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Refresh */}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          <span className="ml-1">Actualizar estado</span>
        </Button>
      </div>

      {/* Last updated */}
      <p className="text-xs text-muted-foreground text-center">
        Última actualización:{" "}
        {new Date(status.last_updated).toLocaleString("es-ES")}
      </p>
    </div>
  );
}

function StatusRow({
  label,
  status,
  detail,
}: {
  label: string;
  status: "ok" | "error" | "unknown";
  detail: string;
}) {
  const icon = {
    ok: <CheckCircle2 className="h-4 w-4 text-emerald-600" />,
    error: <XCircle className="h-4 w-4 text-red-600" />,
    unknown: <HelpCircle className="h-4 w-4 text-muted-foreground" />,
  }[status];

  const badge = {
    ok: (
      <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-700 border-emerald-500/30">
        OK
      </Badge>
    ),
    error: (
      <Badge variant="outline" className="text-xs bg-red-500/10 text-red-700 border-red-500/30">
        Error
      </Badge>
    ),
    unknown: (
      <Badge variant="outline" className="text-xs text-muted-foreground">
        Desconocido
      </Badge>
    ),
  }[status];

  return (
    <div className="flex items-start justify-between gap-3 rounded-md border px-3 py-2">
      <div className="flex items-start gap-2 flex-1 min-w-0">
        {icon}
        <div className="min-w-0">
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground break-words font-mono">
            {detail}
          </p>
        </div>
      </div>
      <div className="flex-shrink-0">{badge}</div>
    </div>
  );
}
