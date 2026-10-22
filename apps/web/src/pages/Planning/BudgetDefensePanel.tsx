import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from "@mui/material";
import { AddOutlined, BusinessOutlined, CheckCircleOutlined, EditOutlined, HighlightOffOutlined, PublishOutlined, SendOutlined } from "@mui/icons-material";
import {
  approveDefenseRequest,
  BudgetDefenseRequest,
  BudgetDefenseStatus,
  createDefenseProject,
  createDefenseRequest,
  Department,
  formatApiError,
  getDefenseRequests,
  getDepartments,
  publishDefenseRequest,
  rejectDefenseRequest,
  submitDefenseRequest,
} from "../../services/api";
import DepartmentTreePicker, {
  flattenDepartments,
} from "../../components/DepartmentTreePicker";
import { ORG } from "../../constants/orgLabels";

const money = (value: number) =>
  Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });

const STATUS_META: Record<
  BudgetDefenseStatus,
  { label: string; color: "default" | "info" | "warning" | "success" | "error" }
> = {
  RASCUNHO: { label: "Rascunho", color: "default" },
  SOLICITADA: { label: "Esperando aprovação", color: "info" },
  APROVADA: { label: "Aprovada, pronta para publicar", color: "warning" },
  PUBLICADA: { label: "Publicada na meta", color: "success" },
  REJEITADA: { label: "Rejeitada", color: "error" },
};

const today = () => new Date().toISOString().slice(0, 10);

/** código de orçamento e cloud herdados pelo nó (o ancestral mais próximo que define). */
function inheritedScope(
  flat: Array<Department & { path: string }>,
  node: Department | null,
): { budgetId: string | null; cloud: string | null; own: boolean } {
  if (!node) return { budgetId: null, cloud: null, own: false };
  const byId = new Map(flat.map((item) => [item.id, item]));
  const up: Department[] = [];
  let current: Department | undefined = node;
  const seen = new Set<number>();
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    up.push(current);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  const ladder = [...up].reverse();
  const budgetId = up.find((item) => item.id_orcamento)?.id_orcamento || null;
  return {
    budgetId,
    cloud: ladder[2]?.name || null,
    own: Boolean(node.id_orcamento),
  };
}

export interface BudgetDefensePanelProps {
  year: number;
  selectedRequestId: number | null;
  canRequest: boolean;
  canPublish: boolean;
  refreshKey?: number;
  onSelectRequest: (request: BudgetDefenseRequest | null) => void;
  onChanged: () => void;
}

export default function BudgetDefensePanel({
  year,
  selectedRequestId,
  canRequest,
  canPublish,
  refreshKey = 0,
  onSelectRequest,
  onChanged,
}: BudgetDefensePanelProps) {
  const [requests, setRequests] = useState<BudgetDefenseRequest[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newRequestOpen, setNewRequestOpen] = useState(false);
  const [approveTarget, setApproveTarget] = useState<BudgetDefenseRequest | null>(null);
  const [publishTarget, setPublishTarget] = useState<BudgetDefenseRequest | null>(null);
  const [rejectTarget, setRejectTarget] = useState<BudgetDefenseRequest | null>(null);

  const [projectForm, setProjectForm] = useState({
    name: "",
    id_orcamento: "",
    centro_custo: "",
    description: "",
  });
  const [requestForm, setRequestForm] = useState({ requested_id_orcamento: "" });
  const [approvalForm, setApprovalForm] = useState({
    final_id_orcamento: "",
    decision_note: "",
    effective_date: today(),
  });
  const [publishForm, setPublishForm] = useState({
    title: "",
    description: "",
    effective_date: today(),
    final_id_orcamento: "",
    decision_note: "",
  });
  const [rejectNote, setRejectNote] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await getDefenseRequests(year);
      setRequests(data);
    } catch (e) {
      setError(formatApiError(e, "Não foi possível abrir as solicitações de defesa"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [year, refreshKey]);

  const loadDepartments = async () => {
    const tree = await getDepartments();
    setDepartments(tree);
    return tree;
  };

  useEffect(() => {
    if (!canRequest && !canPublish) return;
    void loadDepartments().catch((e) =>
      setError(formatApiError(e, "Não foi possível carregar a estrutura organizacional")),
    );
  }, [canRequest, canPublish]);

  // Ao trocar o ano, esqueça a seleção (o estrutura continua válido, mas a
  // solicitação destacada não existe mais).
  useEffect(() => {
    setSelectedDepartmentId(null);
  }, [year]);

  const flatDepartments = useMemo(() => flattenDepartments(departments), [departments]);
  const selectedDepartment = useMemo(
    () => flatDepartments.find((item) => item.id === selectedDepartmentId) || null,
    [flatDepartments, selectedDepartmentId],
  );
  const scope = useMemo(
    () => inheritedScope(flatDepartments, selectedDepartment),
    [flatDepartments, selectedDepartment],
  );

  const selected = useMemo(
    () => requests.find((item) => item.id === selectedRequestId) || null,
    [requests, selectedRequestId],
  );

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setMessage(success);
      await load();
      onChanged();
      return true;
    } catch (e) {
      setError(formatApiError(e, "Não foi possível concluir a ação"));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const requireSelection = () => {
    if (selectedDepartment) return true;
    setError("Escolha na estrutura onde o projeto vai ficar.");
    return false;
  };

  const openNewProject = () => {
    if (!requireSelection()) return;
    setError("");
    setProjectForm({ name: "", id_orcamento: "", centro_custo: "", description: "" });
    setNewProjectOpen(true);
  };

  const openNewRequest = () => {
    if (!requireSelection()) return;
    setError("");
    setRequestForm({ requested_id_orcamento: scope.budgetId || "" });
    setNewRequestOpen(true);
  };

  const submitProject = async () => {
    if (!selectedDepartment) return;
    if (!projectForm.name.trim() || !projectForm.id_orcamento.trim()) {
      setError("Informe o nome do projeto e o novo código de orçamento.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await createDefenseProject({
        year,
        parent_id: selectedDepartment.id,
        name: projectForm.name.trim(),
        id_orcamento: projectForm.id_orcamento.trim(),
        centro_custo: projectForm.centro_custo.trim() || null,
        description: projectForm.description.trim() || null,
      });
      setMessage("Projeto criado na estrutura e solicitação de defesa aberta.");
      setNewProjectOpen(false);
      await load();
      // Recarrega o estrutura com o nó novo e já o deixa selecionado.
      await loadDepartments();
      setSelectedDepartmentId(created.department.id);
      onChanged();
      onSelectRequest(created.request);
    } catch (e) {
      setError(formatApiError(e, "Não foi possível incluir o projeto"));
    } finally {
      setBusy(false);
    }
  };

  const submitRequest = async () => {
    if (!selectedDepartment) return;
    if (!requestForm.requested_id_orcamento.trim()) {
      setError("Informe o código de orçamento solicitado.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await createDefenseRequest({
        year,
        department_id: selectedDepartment.id,
        requested_id_orcamento: requestForm.requested_id_orcamento.trim(),
      });
      setMessage(
        created.is_individual_line
          ? "Solicitação aberta como linha individual: a estrutura só muda na publicação."
          : "Pedido de defesa aberto.",
      );
      setNewRequestOpen(false);
      await load();
      onChanged();
      onSelectRequest(created);
    } catch (e) {
      setError(formatApiError(e, "Não foi possível abrir o pedido"));
    } finally {
      setBusy(false);
    }
  };

  const focusRequest = (request: BudgetDefenseRequest) => {
    if (flatDepartments.some((item) => item.id === request.department_id)) {
      setSelectedDepartmentId(request.department_id);
    }
    onSelectRequest(request);
  };

  const openApprove = (request: BudgetDefenseRequest) => {
    setApproveTarget(request);
    setApprovalForm({
      final_id_orcamento: request.requested_id_orcamento,
      decision_note: "",
      effective_date: today(),
    });
  };

  const submitApprove = async () => {
    if (!approveTarget) return;
    if (!approvalForm.final_id_orcamento.trim() || !approvalForm.decision_note.trim()) {
      setError("Informe o código de orçamento final e o motivo da aprovação.");
      return;
    }
    const ok = await run(
      () =>
        approveDefenseRequest(approveTarget.id, {
          final_id_orcamento: approvalForm.final_id_orcamento.trim(),
          decision_note: approvalForm.decision_note.trim(),
          effective_date: approvalForm.effective_date,
        }),
      "Defesa aprovada. Use Publicar na meta para efetivar a alteração.",
    );
    if (ok) setApproveTarget(null);
  };

  const openPublish = (request: BudgetDefenseRequest) => {
    setPublishTarget(request);
    setPublishForm({
      title: request.title || `Orçamento aprovado ${year}`,
      description: request.decision_note || request.description || "",
      effective_date: today(),
      final_id_orcamento: request.final_id_orcamento || request.requested_id_orcamento,
      decision_note: request.decision_note || "",
    });
  };

  const submitPublish = async () => {
    if (!publishTarget) return;
    if (!publishForm.title.trim() || !publishForm.description.trim() || !publishForm.final_id_orcamento.trim()) {
      setError("Preencha aprovação, motivo e o código de orçamento final antes de publicar.");
      return;
    }
    const ok = await run(
      () =>
        publishDefenseRequest(publishTarget.id, {
          title: publishForm.title.trim(),
          description: publishForm.description.trim(),
          effective_date: publishForm.effective_date,
          final_id_orcamento: publishForm.final_id_orcamento.trim(),
          decision_note: publishForm.decision_note.trim() || null,
        }),
      "Defesa publicada na meta. O projeto passou para o código de orçamento final.",
    );
    if (ok) setPublishTarget(null);
  };

  const submitReject = async () => {
    if (!rejectTarget) return;
    if (!rejectNote.trim()) {
      setError("Explique o motivo da rejeição.");
      return;
    }
    const ok = await run(
      () => rejectDefenseRequest(rejectTarget.id, { decision_note: rejectNote.trim() }),
      "Pedido de defesa recusado.",
    );
    if (ok) {
      setRejectTarget(null);
      setRejectNote("");
    }
  };

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={2} flexWrap="wrap">
        <Box>
          <Typography variant="h6">Pedidos de defesa do projeto</Typography>
          <Typography variant="body2" color="text.secondary">
            Navegue pela estrutura e selecione onde o projeto vai ficar: crie um projeto novo
            naquele nó ou defenda um projeto existente. Projetos existentes defendidos em outro ID
            meta ficam em <b>linha individual</b> até a publicação.
          </Typography>
        </Box>
        <Stack direction="row" gap={1} flexWrap="wrap">
          <Button
            startIcon={<AddOutlined />}
            disabled={!canRequest}
            onClick={openNewProject}
          >
            Incluir projeto
          </Button>
          <Button
            startIcon={<AddOutlined />}
            disabled={!canRequest}
            onClick={openNewRequest}
          >
            Pedir defesa de projeto existente
          </Button>
          <Button onClick={() => void load()} disabled={loading}>
            Atualizar
          </Button>
        </Stack>
      </Stack>

      {!canRequest && (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          Você não tem permissão para solicitar defesas (planejamento · defesa-solicitar).
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError("")}>
          {dataDisplayText(error)}
        </Alert>
      )}
      {message && (
        <Alert severity="success" sx={{ mt: 1.5 }} onClose={() => setMessage("")}>
          {dataDisplayText(message)}
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 1.5, mt: 2 }}>
        <Stack direction={{ xs: "column", md: "row" }} gap={2} alignItems={{ md: "flex-start" }} flexWrap="wrap">
          <Box sx={{ minWidth: 330, flex: 1 }}>
            <DepartmentTreePicker
              departments={departments}
              value={selectedDepartmentId}
              onChange={setSelectedDepartmentId}
              allowClear
              clearLabel="Nenhum"
              label="Onde o projeto será alocado"
              placeholder={ORG.selectNode}
              menuHint={ORG.selectNodeFilterHint}
              helperText={ORG.selectNodeFilterHint}
              startIcon={<BusinessOutlined fontSize="small" />}
            />
          </Box>
          <Box sx={{ mt: { md: "22px" }, minWidth: 260, flex: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Nó selecionado
            </Typography>
            <Typography variant="body2" fontWeight={700}>
              {selectedDepartment?.path || "Nenhum nó escolhido"}
            </Typography>
            <Stack direction="row" gap={2} flexWrap="wrap">
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Código de orçamento {scope.own ? "(próprio)" : "(herdado)"}
                </Typography>
                <Typography variant="body2" fontWeight={700}>
                  {scope.budgetId || "—"}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Cloud
                </Typography>
                <Typography variant="body2" fontWeight={700}>
                  {scope.cloud || "—"}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Centro de custo
                </Typography>
                <Typography variant="body2" fontWeight={700}>
                  {selectedDepartment?.centro_custo || "—"}
                </Typography>
              </Box>
            </Stack>
          </Box>
          <Stack direction="row" gap={1} sx={{ mt: { md: "22px" }, alignItems: "center" }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<AddOutlined />}
              disabled={!canRequest || !selectedDepartment}
              onClick={openNewProject}
            >
              Criar projeto aqui
            </Button>
            <Button
              size="small"
              variant="outlined"
              startIcon={<AddOutlined />}
              disabled={!canRequest || !selectedDepartment}
              onClick={openNewRequest}
            >
              Defender este projeto
            </Button>
          </Stack>
        </Stack>
      </Paper>

      <Divider sx={{ my: 2 }} />

      {selected && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Editando a defesa de <b>{selected.project_name}</b> no código de orçamento{" "}
          <b>{selected.requested_id_orcamento}</b>. Use <b>Salvar</b> na grade abaixo para gravar
          os valores; <b>Enviar</b> submete para aprovação.
        </Alert>
      )}

      {loading && !requests.length ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={24} />
        </Box>
      ) : requests.length === 0 ? (
        <Typography color="text.secondary">
          Nenhuma solicitação de defesa para {year}. Escolha um nó na estrutura e use{" "}
          <b>Incluir projeto</b> ou <b>Pedir defesa de projeto existente</b>.
        </Typography>
      ) : (
        <Box sx={{ overflowX: "auto" }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Projeto</TableCell>
                <TableCell>Caminho na estrutura</TableCell>
                <TableCell>Código pedido</TableCell>
                <TableCell>Código atual (estrutura)</TableCell>
                <TableCell align="right">Total do ano</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Ações</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {requests.map((request) => {
                const meta = STATUS_META[request.status];
                const isSelected = request.id === selectedRequestId;
                return (
                  <TableRow
                    key={request.id}
                    hover
                    selected={isSelected}
                    sx={{ cursor: "pointer" }}
                    onClick={() => focusRequest(request)}
                  >
                    <TableCell>
                      <Stack direction="row" alignItems="center" gap={0.75}>
                        <Typography fontWeight={600}>{request.project_name || "—"}</Typography>
                        {request.is_new_project && <Chip size="small" label="Novo" color="secondary" />}
                        {request.is_individual_line && (
                          <Tooltip title="Defendido em um código de orçamento diferente da estrutura. Ao publicar, o projeto passa para o código final.">
                            <Chip size="small" label="Linha avulsa" color="warning" variant="outlined" />
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary" noWrap maxWidth={280}>
                        {request.project_path?.join(" › ") || "—"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography fontWeight={600}>{request.requested_id_orcamento}</Typography>
                      {request.requested_cloud ? (
                        <Typography variant="caption" color="text.secondary">
                          {request.requested_cloud}
                        </Typography>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      {request.organograma_id_orcamento || (
                        <Typography variant="body2" color="text.secondary">—</Typography>
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Typography fontWeight={700}>{money(request.annual_total)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={meta.color} label={meta.label} />
                    </TableCell>
                    <TableCell align="right" onClick={(event) => event.stopPropagation()}>
                      <Stack direction="row" gap={0.5} justifyContent="flex-end" flexWrap="wrap">
                        <Tooltip title="Alterar a defesa deste projeto na grade abaixo">
                          <Button size="small" startIcon={<EditOutlined />} onClick={() => focusRequest(request)}>
                            Editar defesa
                          </Button>
                        </Tooltip>
                        {canRequest && request.status === "RASCUNHO" && (
                          <Tooltip title="Encaminhar a defesa para aprovação">
                            <span>
                              <Button
                                size="small"
                                startIcon={<SendOutlined />}
                                disabled={busy || !request.defense_version_id}
                                onClick={() =>
                                  void run(
                                    () => submitDefenseRequest(request.id),
                                    "Defesa encaminhada para aprovação.",
                                  )
                                }
                              >
                                Enviar
                              </Button>
                            </span>
                          </Tooltip>
                        )}
                        {canPublish && request.status === "SOLICITADA" && (
                          <Button
                            size="small"
                            variant="outlined"
                            startIcon={<CheckCircleOutlined />}
                            onClick={() => openApprove(request)}
                          >
                            Aprovar
                          </Button>
                        )}
                        {canPublish && request.status === "APROVADA" && (
                          <Button
                            size="small"
                            variant="contained"
                            startIcon={<PublishOutlined />}
                            onClick={() => openPublish(request)}
                          >
                            Publicar na meta
                          </Button>
                        )}
                        {canPublish && request.status !== "PUBLICADA" && request.status !== "REJEITADA" && (
                          <Tooltip title="Recusar a solicitação">
                            <IconButton
                              size="small"
                              onClick={() => {
                                setRejectTarget(request);
                                setRejectNote("");
                              }}
                            >
                              <HighlightOffOutlined fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      )}

      {/* Incluir projeto ------------------------------------------------------ */}
      <Dialog open={newProjectOpen} onClose={() => !busy && setNewProjectOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Incluir projeto na defesa orçamentária</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Alert severity="info">
              O projeto será criado na estrutura em <b>{selectedDepartment?.path || "—"}</b>, já
              com o novo código de orçamento.
            </Alert>
            <TextField
              label="Denominação do projeto"
              value={projectForm.name}
              onChange={(event) => setProjectForm((form) => ({ ...form, name: event.target.value }))}
              required
            />
            <TextField
              label="Novo código de orçamento"
              value={projectForm.id_orcamento}
              onChange={(event) => setProjectForm((form) => ({ ...form, id_orcamento: event.target.value }))}
              required
            />
            <TextField
              label="Centro de custo (opcional)"
              value={projectForm.centro_custo}
              onChange={(event) => setProjectForm((form) => ({ ...form, centro_custo: event.target.value }))}
            />
            <TextField
              label="Motivo da inclusão"
              multiline
              minRows={2}
              value={projectForm.description}
              onChange={(event) => setProjectForm((form) => ({ ...form, description: event.target.value }))}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewProjectOpen(false)}>Cancelar</Button>
          <Button variant="contained" disabled={busy} onClick={() => void submitProject()}>
            Criar projeto e solicitar defesa
          </Button>
        </DialogActions>
      </Dialog>

      {/* Pedir defesa de projeto existente ----------------------------- */}
      <Dialog open={newRequestOpen} onClose={() => !busy && setNewRequestOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Pedir defesa de projeto existente</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Alert severity="warning">
              A estrutura <b>não</b> é alterada agora. Se o código de orçamento solicitado for diferente
              do herdado, o projeto aparece como linha individual na defesa e só muda de linha na
              publicação.
            </Alert>
            <Box sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", border: "1px solid", borderColor: "divider" }}>
              <Typography variant="caption" color="text.secondary">Projeto escolhido</Typography>
              <Typography variant="body2" fontWeight={700}>{selectedDepartment?.path || "—"}</Typography>
              <Typography variant="caption" color="text.secondary">
                Código de orçamento atual: {scope.budgetId || "—"}
              </Typography>
            </Box>
            <TextField
              label="Código de orçamento solicitado"
              value={requestForm.requested_id_orcamento}
              onChange={(event) =>
                setRequestForm((form) => ({ ...form, requested_id_orcamento: event.target.value }))
              }
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewRequestOpen(false)}>Cancelar</Button>
          <Button variant="contained" disabled={busy} onClick={() => void submitRequest()}>
            Abrir solicitação
          </Button>
        </DialogActions>
      </Dialog>

      {/* Aprovar ------------------------------------------------------------ */}
      <Dialog open={Boolean(approveTarget)} onClose={() => !busy && setApproveTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Aprovar a defesa</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Alert severity="info">
              O analista decide se mantém o projeto no código de orçamento solicitado (
              <b>{approveTarget?.requested_id_orcamento}</b>) ou o move para outro. A mudança na
              hierarquia acontece somente na publicação.
            </Alert>
            <TextField
              label="Código de orçamento final"
              value={approvalForm.final_id_orcamento}
              onChange={(event) =>
                setApprovalForm((form) => ({ ...form, final_id_orcamento: event.target.value }))
              }
              required
            />
            <TextField
              label="Data de aprovação"
              type="date"
              InputLabelProps={{ shrink: true }}
              value={approvalForm.effective_date}
              onChange={(event) =>
                setApprovalForm((form) => ({ ...form, effective_date: event.target.value }))
              }
            />
            <TextField
              label="Motivo e comentários da decisão"
              multiline
              minRows={3}
              value={approvalForm.decision_note}
              onChange={(event) =>
                setApprovalForm((form) => ({ ...form, decision_note: event.target.value }))
              }
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setApproveTarget(null)}>Cancelar</Button>
          <Button variant="contained" disabled={busy} onClick={() => void submitApprove()}>
            Aprovar
          </Button>
        </DialogActions>
      </Dialog>

      {/* Publicar ----------------------------------------------------------- */}
      <Dialog open={Boolean(publishTarget)} onClose={() => !busy && setPublishTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Publicar defesa na meta</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Alert severity="warning">
              Será criado um snapshot de meta para {year} substituindo somente o escopo{" "}
              <b>{publishForm.final_id_orcamento || "a definir"}</b>. O projeto{" "}
              <b>{publishTarget?.project_name}</b> passará a pertencer ao código de orçamento final e
              deixará de ser linha individual.
            </Alert>
            <TextField
              label="Aprovação"
              value={publishForm.title}
              onChange={(event) => setPublishForm((form) => ({ ...form, title: event.target.value }))}
              required
            />
            <TextField
              label="Código de orçamento final"
              value={publishForm.final_id_orcamento}
              onChange={(event) =>
                setPublishForm((form) => ({ ...form, final_id_orcamento: event.target.value }))
              }
              required
            />
            <TextField
              label="Data de publicação"
              type="date"
              InputLabelProps={{ shrink: true }}
              value={publishForm.effective_date}
              onChange={(event) =>
                setPublishForm((form) => ({ ...form, effective_date: event.target.value }))
              }
            />
            <TextField
              label="Motivo e comentários"
              multiline
              minRows={3}
              value={publishForm.description}
              onChange={(event) =>
                setPublishForm((form) => ({ ...form, description: event.target.value }))
              }
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPublishTarget(null)}>Cancelar</Button>
          <Button variant="contained" disabled={busy} onClick={() => void submitPublish()}>
            Publicar na meta
          </Button>
        </DialogActions>
      </Dialog>

      {/* Rejeitar ----------------------------------------------------------- */}
      <Dialog open={Boolean(rejectTarget)} onClose={() => !busy && setRejectTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Recusar solicitação de defesa</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              {rejectTarget?.project_name} · {rejectTarget?.requested_id_orcamento}
            </Typography>
            <TextField
              label="Motivo da recusa"
              multiline
              minRows={3}
              value={rejectNote}
              onChange={(event) => setRejectNote(event.target.value)}
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectTarget(null)}>Cancelar</Button>
          <Button color="error" variant="contained" disabled={busy} onClick={() => void submitReject()}>
            Rejeitar
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}
