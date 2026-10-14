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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  ClipboardCheck,
  Check,
  X,
  AlertCircle,
  Clock,
  User,
  RefreshCw,
  Ban,
} from "lucide-react";
import {
  fetchPendingApprovals,
  postApprovalDecision,
  type PendingApproval,
} from "@/lib/api";
import { toast } from "sonner";

export function ApprovalsView() {
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [available, setAvailable] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedApproval, setSelectedApproval] = useState<PendingApproval | null>(
    null,
  );
  const [decisionDialog, setDecisionDialog] = useState<{
    approval: PendingApproval;
    decision: "approve" | "reject";
  } | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [approverName, setApproverName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // Track in-flight approvals to prevent double-clicks
  const [inFlight, setInFlight] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setRefreshing(true);
    const result = await fetchPendingApprovals();
    setApprovals(result.approvals);
    setAvailable(result.available);
    setError(result.error || null);
    setIsDemo(result.is_demo);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setRefreshing(true);
      const result = await fetchPendingApprovals();
      if (cancelled) return;
      setApprovals(result.approvals);
      setAvailable(result.available);
      setError(result.error || null);
      setIsDemo(result.is_demo);
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

  const handleDecision = async () => {
    if (!decisionDialog) return;
    const { approval, decision } = decisionDialog;
    if (inFlight.has(approval.approval_id)) return;
    if (decision === "reject" && !decisionReason.trim()) {
      toast.error("El motivo de rechazo es obligatorio");
      return;
    }
    if (!approverName.trim()) {
      toast.error("Indica tu identificador como aprobador");
      return;
    }

    setSubmitting(true);
    setInFlight((prev) => new Set(prev).add(approval.approval_id));

    const result = await postApprovalDecision(approval.approval_id, {
      decision,
      approver: approverName.trim(),
      reason: decisionReason.trim() || undefined,
    });

    setSubmitting(false);
    setInFlight((prev) => {
      const next = new Set(prev);
      next.delete(approval.approval_id);
      return next;
    });

    if (result.success) {
      toast.success(
        decision === "approve"
          ? "Aprobación concedida — el backend procesará la decisión"
          : "Aprobación rechazada — el backend procesará la decisión",
      );
      setDecisionDialog(null);
      setDecisionReason("");
      setApproverName("");
      // Refresh the list
      load();
    } else {
      toast.error(
        `No se pudo procesar la decisión: ${result.error || "error desconocido"}`,
      );
    }
  };

  if (loading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  // Backend does not expose approval routes
  if (!available) {
    return (
      <div className="grid gap-4">
        <Alert variant="default">
          <Ban className="h-4 w-4" />
          <AlertTitle>Función no disponible</AlertTitle>
          <AlertDescription>
            El backend no expone rutas de aprobaciones. Las siguientes rutas
            sugeridas devuelven 404:
          </AlertDescription>
        </Alert>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Endpoints pendientes de implementar</CardTitle>
            <CardDescription>
              El componente ApprovalEngine existe en el backend pero no está
              conectado a ninguna ruta HTTP.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2">
              {[
                {
                  method: "GET",
                  path: "/api/v1/approvals/pending",
                  purpose: "Listar aprobaciones pendientes",
                },
                {
                  method: "POST",
                  path: "/api/v1/approvals/{approval_id}/decision",
                  purpose: "Aprobar o rechazar una solicitud",
                },
                {
                  method: "POST",
                  path: "/api/v1/approvals/{approval_id}/resume",
                  purpose: "Reanudar la ejecución tras una decisión",
                },
              ].map((ep) => (
                <div
                  key={ep.path}
                  className="flex flex-col sm:flex-row sm:items-center gap-2 rounded-md border px-3 py-2"
                >
                  <Badge variant="outline" className="font-mono text-xs w-fit">
                    {ep.method}
                  </Badge>
                  <code className="text-xs font-mono text-foreground flex-1">
                    {ep.path}
                  </code>
                  <span className="text-xs text-muted-foreground">
                    {ep.purpose}
                  </span>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-4">
              Implementar estas rutas requiere cambios en el backend que deben
              pasar por el proceso de revisión del auditor independiente. El
              dashboard no puede eludir los controles HITL existentes.
            </p>
          </CardContent>
        </Card>

        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Error al consultar aprobaciones</AlertTitle>
            <AlertDescription className="font-mono text-xs">{error}</AlertDescription>
          </Alert>
        )}

        {isDemo && approvals.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <ClipboardCheck className="h-4 w-4" />
                Datos de demostración
              </CardTitle>
              <CardDescription>
                Vista previa de cómo se verían las aprobaciones cuando el
                backend implemente las rutas. Estas NO son aprobaciones reales.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-2">
                {approvals.map((a) => (
                  <ApprovalRow
                    key={a.approval_id}
                    approval={a}
                    isDemo
                    onSelect={() => setSelectedApproval(a)}
                  />
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {approvals.length === 0
            ? "No hay aprobaciones pendientes"
            : `${approvals.length} solicitud(es) esperando decisión humana`}
        </p>
        <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          <span className="ml-1 hidden sm:inline">Actualizar</span>
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription className="font-mono text-xs">{error}</AlertDescription>
        </Alert>
      )}

      {approvals.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <ClipboardCheck className="h-10 w-10 text-muted-foreground/50" />
            <p className="mt-3 text-sm text-muted-foreground">
              Sin solicitudes pendientes
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-2">
          {approvals.map((a) => (
            <ApprovalRow
              key={a.approval_id}
              approval={a}
              isDemo={isDemo}
              onSelect={() => setSelectedApproval(a)}
              onApprove={() =>
                setDecisionDialog({ approval: a, decision: "approve" })
              }
              onReject={() =>
                setDecisionDialog({ approval: a, decision: "reject" })
              }
              disabled={inFlight.has(a.approval_id)}
            />
          ))}
        </div>
      )}

      {/* Detail dialog */}
      <Dialog
        open={selectedApproval !== null}
        onOpenChange={(open) => !open && setSelectedApproval(null)}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Detalle de aprobación</DialogTitle>
            <DialogDescription>
              Revisa el contexto disponible antes de decidir. El backend es la
              autoridad — esta vista no ejecuta nada.
            </DialogDescription>
          </DialogHeader>
          {selectedApproval && (
            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label className="text-xs text-muted-foreground">ID</Label>
                <code className="text-xs font-mono bg-muted px-2 py-1 rounded">
                  {selectedApproval.approval_id}
                </code>
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs text-muted-foreground">Acción</Label>
                <code className="text-xs font-mono bg-muted px-2 py-1 rounded">
                  {selectedApproval.action}
                </code>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <Label className="text-xs text-muted-foreground flex items-center gap-1">
                    <User className="h-3 w-3" /> Solicitado por
                  </Label>
                  <span className="text-sm">
                    {selectedApproval.requested_by || "desconocido"}
                  </span>
                </div>
                <div className="grid gap-1.5">
                  <Label className="text-xs text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" /> Solicitado el
                  </Label>
                  <span className="text-sm">
                    {new Date(selectedApproval.requested_at).toLocaleString("es-ES")}
                  </span>
                </div>
              </div>
              {selectedApproval.context_ref && (
                <div className="grid gap-1.5">
                  <Label className="text-xs text-muted-foreground">Contexto</Label>
                  <pre className="text-xs font-mono bg-muted p-3 rounded overflow-x-auto max-h-48 overflow-y-auto">
                    {JSON.stringify(selectedApproval.context_ref, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setSelectedApproval(null)}
            >
              Cerrar
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (selectedApproval) {
                  setDecisionDialog({
                    approval: selectedApproval,
                    decision: "reject",
                  });
                  setSelectedApproval(null);
                }
              }}
              disabled={!available}
            >
              <X className="h-4 w-4 mr-1" /> Rechazar
            </Button>
            <Button
              variant="default"
              onClick={() => {
                if (selectedApproval) {
                  setDecisionDialog({
                    approval: selectedApproval,
                    decision: "approve",
                  });
                  setSelectedApproval(null);
                }
              }}
              disabled={!available}
            >
              <Check className="h-4 w-4 mr-1" /> Aprobar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Decision confirmation dialog */}
      <AlertDialog
        open={decisionDialog !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDecisionDialog(null);
            setDecisionReason("");
            setApproverName("");
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {decisionDialog?.decision === "approve"
                ? "Confirmar aprobación"
                : "Confirmar rechazo"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              Esta acción será enviada al backend para su procesamiento. El
              agente no procederá hasta que el backend confirme la decisión.
              {decisionDialog?.decision === "reject" &&
                " El motivo de rechazo es obligatorio."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-1.5">
              <Label htmlFor="approver-name" className="text-xs">
                Tu identificador como aprobador
              </Label>
              <input
                id="approver-name"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="ej. human:alice"
                value={approverName}
                onChange={(e) => setApproverName(e.target.value)}
                autoComplete="off"
              />
            </div>
            {decisionDialog?.decision === "reject" && (
              <div className="grid gap-1.5">
                <Label htmlFor="reject-reason" className="text-xs">
                  Motivo del rechazo (obligatorio)
                </Label>
                <Textarea
                  id="reject-reason"
                  placeholder="Explica por qué se rechaza esta solicitud..."
                  value={decisionReason}
                  onChange={(e) => setDecisionReason(e.target.value)}
                  rows={3}
                />
              </div>
            )}
            {decisionDialog?.decision === "approve" && (
              <div className="grid gap-1.5">
                <Label htmlFor="approve-reason" className="text-xs">
                  Comentario (opcional)
                </Label>
                <Textarea
                  id="approve-reason"
                  placeholder="Comentario sobre la aprobación..."
                  value={decisionReason}
                  onChange={(e) => setDecisionReason(e.target.value)}
                  rows={2}
                />
              </div>
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleDecision();
              }}
              disabled={submitting}
              className={
                decisionDialog?.decision === "approve"
                  ? "bg-emerald-600 hover:bg-emerald-700"
                  : ""
              }
            >
              {submitting
                ? "Procesando..."
                : decisionDialog?.decision === "approve"
                  ? "Confirmar aprobación"
                  : "Confirmar rechazo"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

interface ApprovalRowProps {
  approval: PendingApproval;
  isDemo: boolean;
  onSelect: () => void;
  onApprove?: () => void;
  onReject?: () => void;
  disabled?: boolean;
}

function ApprovalRow({
  approval,
  isDemo,
  onSelect,
  onApprove,
  onReject,
  disabled,
}: ApprovalRowProps) {
  return (
    <Card>
      <CardContent className="p-3 sm:p-4">
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <code className="text-xs font-mono text-muted-foreground">
                {approval.approval_id.slice(0, 12)}
              </code>
              <Badge variant="outline" className="text-xs">
                {approval.status}
              </Badge>
              {isDemo && (
                <Badge variant="outline" className="text-xs text-amber-700 border-amber-500/30">
                  demo
                </Badge>
              )}
            </div>
            <p className="mt-1.5 text-sm font-mono truncate">
              {approval.action}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <User className="h-3 w-3" />
                {approval.requested_by || "desconocido"}
              </span>
              <span className="inline-flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {new Date(approval.requested_at).toLocaleString("es-ES", {
                  hour: "2-digit",
                  minute: "2-digit",
                  day: "2-digit",
                  month: "2-digit",
                })}
              </span>
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={onSelect}>
              Ver detalle
            </Button>
            {onApprove && (
              <Button
                size="sm"
                variant="default"
                onClick={onApprove}
                disabled={disabled || isDemo}
                className="bg-emerald-600 hover:bg-emerald-700"
              >
                <Check className="h-3.5 w-3.5 mr-1" /> Aprobar
              </Button>
            )}
            {onReject && (
              <Button
                size="sm"
                variant="destructive"
                onClick={onReject}
                disabled={disabled || isDemo}
              >
                <X className="h-3.5 w-3.5 mr-1" /> Rechazar
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
