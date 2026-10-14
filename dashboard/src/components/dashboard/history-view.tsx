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
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  History,
  Search,
  AlertCircle,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Clock,
} from "lucide-react";
import { fetchExecutionTrace, type ExecutionTrace } from "@/lib/api";

interface HistoryEntry {
  execution_id: string;
  status: "approved" | "executed" | "uncertain" | "denied" | "unknown";
  action?: string;
  timestamp: string;
  decided_by?: string;
  result?: "success" | "failure" | "unknown";
}

export function HistoryView() {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selectedEntry, setSelectedEntry] = useState<HistoryEntry | null>(null);
  const [trace, setTrace] = useState<ExecutionTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // The backend does not expose a "list executions" endpoint.
      // We attempt to fetch a known execution_id (or empty) to demonstrate
      // the trace lookup. In a real deployment, this would be a list call.
      // For now, we show demo data clearly flagged.
      const demoEntries: HistoryEntry[] = [
        {
          execution_id: "demo-exec-001",
          status: "executed",
          action: "tool.echo",
          timestamp: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
          result: "success",
        },
        {
          execution_id: "demo-exec-002",
          status: "approved",
          action: "tool.external.http",
          timestamp: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
          decided_by: "human:alice",
        },
        {
          execution_id: "demo-exec-003",
          status: "denied",
          action: "tool.destructive.wipe",
          timestamp: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
          decided_by: "human:bob",
        },
        {
          execution_id: "demo-exec-004",
          status: "uncertain",
          action: "tool.external.smtp",
          timestamp: new Date(Date.now() - 1000 * 60 * 120).toISOString(),
        },
      ];
      setEntries(demoEntries);
      setIsDemo(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error desconocido");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        const demoEntries: HistoryEntry[] = [
          {
            execution_id: "demo-exec-001",
            status: "executed",
            action: "tool.echo",
            timestamp: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
            result: "success",
          },
          {
            execution_id: "demo-exec-002",
            status: "approved",
            action: "tool.external.http",
            timestamp: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
            decided_by: "human:alice",
          },
          {
            execution_id: "demo-exec-003",
            status: "denied",
            action: "tool.destructive.wipe",
            timestamp: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
            decided_by: "human:bob",
          },
          {
            execution_id: "demo-exec-004",
            status: "uncertain",
            action: "tool.external.smtp",
            timestamp: new Date(Date.now() - 1000 * 60 * 120).toISOString(),
          },
        ];
        if (cancelled) return;
        setEntries(demoEntries);
        setIsDemo(true);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "error desconocido");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadTrace = useCallback(async (executionId: string) => {
    setTraceLoading(true);
    setTrace(null);
    const result = await fetchExecutionTrace(executionId);
    setTrace(result);
    setTraceLoading(false);
  }, []);

  const filtered = entries.filter((e) => {
    if (statusFilter !== "all" && e.status !== statusFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        e.execution_id.toLowerCase().includes(q) ||
        (e.action?.toLowerCase().includes(q) ?? false) ||
        (e.decided_by?.toLowerCase().includes(q) ?? false)
      );
    }
    return true;
  });

  if (loading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <Alert variant="default">
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>Integración parcial</AlertTitle>
        <AlertDescription>
          El backend expone <code className="text-xs font-mono">GET /api/v1/execution/{`{id}`}</code>{" "}
          para consultar trazas individuales, pero no un endpoint de listado.
          Los registros below son demostrativos. Para ver una traza real,
          introduce un execution_id conocido.
        </AlertDescription>
      </Alert>

      {/* Filters */}
      <Card>
        <CardContent className="p-3 sm:p-4">
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Buscar por execution_id, acción o aprobador..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8"
              />
            </div>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-full sm:w-48">
                <SelectValue placeholder="Estado" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos los estados</SelectItem>
                <SelectItem value="approved">Aprobada (sin ejecutar)</SelectItem>
                <SelectItem value="executed">Ejecutada</SelectItem>
                <SelectItem value="denied">Denegada</SelectItem>
                <SelectItem value="uncertain">Resultado incierto</SelectItem>
                <SelectItem value="unknown">Desconocido</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription className="font-mono text-xs">{error}</AlertDescription>
        </Alert>
      )}

      {/* Manual trace lookup */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Consultar traza por ID</CardTitle>
          <CardDescription>
            Introduce un execution_id para obtener la traza completa del backend.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Input
              placeholder="execution_id..."
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const value = (e.target as HTMLInputElement).value.trim();
                  if (value) loadTrace(value);
                }
              }}
              id="trace-input"
            />
            <Button
              onClick={() => {
                const input = document.getElementById(
                  "trace-input",
                ) as HTMLInputElement;
                const value = input?.value.trim();
                if (value) loadTrace(value);
              }}
            >
              Consultar
            </Button>
          </div>
          {traceLoading && (
            <div className="mt-3">
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {trace && !traceLoading && (
            <div className="mt-3 grid gap-2">
              {trace.fetch_error && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription className="font-mono text-xs">
                    {trace.fetch_error}
                  </AlertDescription>
                </Alert>
              )}
              {trace.is_demo && (
                <Badge variant="outline" className="text-amber-700 border-amber-500/30 w-fit">
                  datos simulados
                </Badge>
              )}
              {trace.found && trace.trace.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">
                    Traza ({trace.trace.length} pasos):
                  </p>
                  <pre className="text-xs font-mono bg-muted p-3 rounded overflow-x-auto max-h-64 overflow-y-auto">
                    {JSON.stringify(trace.trace, null, 2)}
                  </pre>
                </div>
              )}
              {trace.found && trace.trace.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Traza vacía — la ejecución no registró pasos.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="h-4 w-4" />
            Historial de ejecuciones
          </CardTitle>
          <CardDescription>
            {filtered.length} registro(s)
            {isDemo && (
              <span className="ml-2 text-amber-700">· datos de demostración</span>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {/* Desktop table */}
          <div className="hidden md:block">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50">
                <tr>
                  <th className="text-left font-medium px-4 py-2">Estado</th>
                  <th className="text-left font-medium px-4 py-2">Execution ID</th>
                  <th className="text-left font-medium px-4 py-2">Acción</th>
                  <th className="text-left font-medium px-4 py-2">Decidido por</th>
                  <th className="text-left font-medium px-4 py-2">Fecha</th>
                  <th className="text-left font-medium px-4 py-2">Resultado</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => (
                  <tr
                    key={entry.execution_id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => setSelectedEntry(entry)}
                  >
                    <td className="px-4 py-2">
                      <StatusBadge status={entry.status} />
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {entry.execution_id.slice(0, 16)}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {entry.action || "—"}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {entry.decided_by || "—"}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {new Date(entry.timestamp).toLocaleString("es-ES", {
                        hour: "2-digit",
                        minute: "2-digit",
                        day: "2-digit",
                        month: "2-digit",
                      })}
                    </td>
                    <td className="px-4 py-2">
                      <ResultBadge result={entry.result} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden divide-y">
            {filtered.map((entry) => (
              <button
                key={entry.execution_id}
                onClick={() => setSelectedEntry(entry)}
                className="w-full text-left p-3 hover:bg-muted/30"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <StatusBadge status={entry.status} />
                  <span className="text-xs text-muted-foreground">
                    {new Date(entry.timestamp).toLocaleString("es-ES", {
                      hour: "2-digit",
                      minute: "2-digit",
                      day: "2-digit",
                      month: "2-digit",
                    })}
                  </span>
                </div>
                <code className="text-xs font-mono block">
                  {entry.execution_id.slice(0, 20)}
                </code>
                <p className="text-sm font-mono mt-1">{entry.action || "—"}</p>
                {entry.decided_by && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Decidido por: {entry.decided_by}
                  </p>
                )}
              </button>
            ))}
          </div>

          {filtered.length === 0 && (
            <div className="text-center py-12">
              <History className="h-8 w-8 text-muted-foreground/50 mx-auto" />
              <p className="mt-2 text-sm text-muted-foreground">
                Sin registros que coincidan con los filtros
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Detail dialog */}
      <Dialog
        open={selectedEntry !== null}
        onOpenChange={(open) => !open && setSelectedEntry(null)}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Detalle de ejecución</DialogTitle>
            <DialogDescription>
              Información disponible para esta ejecución.
            </DialogDescription>
          </DialogHeader>
          {selectedEntry && (
            <div className="grid gap-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xs text-muted-foreground">Estado</p>
                  <StatusBadge status={selectedEntry.status} />
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Resultado</p>
                  <ResultBadge result={selectedEntry.result} />
                </div>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-1">Execution ID</p>
                <code className="text-xs font-mono bg-muted px-2 py-1 rounded block break-all">
                  {selectedEntry.execution_id}
                </code>
              </div>
              {selectedEntry.action && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Acción</p>
                  <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
                    {selectedEntry.action}
                  </code>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                {selectedEntry.decided_by && (
                  <div>
                    <p className="text-xs text-muted-foreground">Decidido por</p>
                    <p className="text-sm">{selectedEntry.decided_by}</p>
                  </div>
                )}
                <div>
                  <p className="text-xs text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" /> Fecha
                  </p>
                  <p className="text-sm">
                    {new Date(selectedEntry.timestamp).toLocaleString("es-ES")}
                  </p>
                </div>
              </div>
              <div className="rounded-md border p-3 bg-muted/30">
                <p className="text-xs text-muted-foreground mb-2">
                  Status explanation:
                </p>
                <p className="text-xs">
                  {statusExplanation(selectedEntry.status)}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => loadTrace(selectedEntry.execution_id)}
                disabled={traceLoading}
              >
                {traceLoading ? "Cargando traza..." : "Cargar traza completa"}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatusBadge({ status }: { status: HistoryEntry["status"] }) {
  const config: Record<
    HistoryEntry["status"],
    { label: string; variant: "default" | "secondary" | "outline" | "destructive"; className: string; icon: React.ReactNode }
  > = {
    approved: {
      label: "Aprobada",
      variant: "secondary",
      className: "bg-blue-500/10 text-blue-700 border-blue-500/30",
      icon: <CheckCircle2 className="h-3 w-3 mr-1" />,
    },
    executed: {
      label: "Ejecutada",
      variant: "default",
      className: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30",
      icon: <CheckCircle2 className="h-3 w-3 mr-1" />,
    },
    denied: {
      label: "Denegada",
      variant: "destructive",
      className: "",
      icon: <XCircle className="h-3 w-3 mr-1" />,
    },
    uncertain: {
      label: "Incierta",
      variant: "secondary",
      className: "bg-amber-500/10 text-amber-700 border-amber-500/30",
      icon: <HelpCircle className="h-3 w-3 mr-1" />,
    },
    unknown: {
      label: "Desconocida",
      variant: "outline",
      className: "",
      icon: <HelpCircle className="h-3 w-3 mr-1" />,
    },
  };
  const c = config[status];
  return (
    <Badge variant={c.variant} className={`text-xs ${c.className}`}>
      {c.icon}
      {c.label}
    </Badge>
  );
}

function ResultBadge({ result }: { result?: HistoryEntry["result"] }) {
  if (!result || result === "unknown") {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  if (result === "success") {
    return (
      <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-700 border-emerald-500/30">
        <CheckCircle2 className="h-3 w-3 mr-1" /> success
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-xs bg-red-500/10 text-red-700 border-red-500/30">
      <XCircle className="h-3 w-3 mr-1" /> failure
    </Badge>
  );
}

function statusExplanation(status: HistoryEntry["status"]): string {
  const explanations: Record<HistoryEntry["status"], string> = {
    approved:
      "La acción fue aprobada por un humano pero aún no se ha ejecutado, o su ejecución está en proceso.",
    executed:
      "La acción fue ejecutada y el backend registró el resultado.",
    denied:
      "La acción fue denegada — por policy engine, por rechazo humano, o por token inválido. No se ejecutó.",
    uncertain:
      "El resultado de la acción no pudo verificarse — posiblemente por timeout, error de red, o respuesta ambigua del backend.",
    unknown:
      "El estado no pudo determinarse a partir de los datos disponibles.",
  };
  return explanations[status];
}
