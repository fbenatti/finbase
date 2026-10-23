import type { PivotAggregation, PivotFilterOperator, PivotMeasure } from "../services/api";

// Business-facing names for the pivot. Column names come straight from the
// prepared tables, so unknown names are humanised instead of mapped.
const FIELD_LABELS: Record<string, string> = {
  _billing_period: "Mês de cobrança",
  period: "Mês",
  forecast_month: "Mês do forecast",
  origem: "Origem",
  currency: "Moeda",
  realizado: "Realizado",
  value: "Valor",
  tipo: "Tipo",
  centro_custo: "Centro de custo",
  ambiente: "Ambiente",
  consumo_realizado: "Consumo realizado",
  ajuste_contratual: "Ajuste contratual",
  id_orcamento: "ID do orçamento",
  // FOCUS columns arrive lower-cased without separators.
  billingaccountid: "ID da conta de faturamento",
  billingaccountname: "Conta de faturamento",
  billingaccounttype: "Tipo de conta de faturamento",
  subaccountid: "ID da subconta",
  subaccountname: "Subconta",
  subaccounttype: "Tipo de subconta",
  billedcost: "Custo faturado",
  effectivecost: "Custo efetivo",
  listcost: "Custo de lista",
  contractedcost: "Custo contratado",
  consumedquantity: "Quantidade consumida",
  consumedunit: "Unidade de consumo",
  billingcurrency: "Moeda de faturamento",
  chargecategory: "Categoria da cobrança",
  chargeclass: "Classe da cobrança",
  chargedescription: "Descrição da cobrança",
  chargefrequency: "Frequência da cobrança",
  chargeperiodstart: "Início da cobrança",
  chargeperiodend: "Fim da cobrança",
  billingperiodstart: "Início do período de faturamento",
  billingperiodend: "Fim do período de faturamento",
  servicename: "Serviço",
  servicecategory: "Categoria do serviço",
  servicesubcategory: "Subcategoria do serviço",
  serviceprovidername: "Provedor do serviço",
  hostprovidername: "Provedor de hospedagem",
  invoiceissuername: "Emissor da fatura",
  invoiceid: "Fatura",
  regionid: "ID da região",
  regionname: "Região",
  availabilityzone: "Zona de disponibilidade",
  resourceid: "ID do recurso",
  resourcename: "Recurso",
  resourcetype: "Tipo de recurso",
  skuid: "SKU",
  pricingcategory: "Categoria de preço",
  pricingunit: "Unidade de preço",
  pricingquantity: "Quantidade precificada",
  commitmentdiscountname: "Desconto de compromisso",
  commitmentdiscounttype: "Tipo de desconto de compromisso",
  tags: "Tags",
};

// Words used to split the remaining FOCUS names ("commitmentdiscountstatus"
// → "Commitment discount status"); longest match wins.
const FOCUS_WORDS = ["applicability", "availability", "eligibility", "fulfillment", "subcategory", "percentage", "reservation", "commitment", "contracted", "allocated", "frequency", "lifecycle", "reference", "effective", "consumed", "currency", "discount", "duration", "category", "capacity", "interval", "provider", "purchase", "quantity", "resource", "contract", "description", "benefit", "billing", "created", "details", "invoice", "payment", "pricing", "program", "service", "updated", "account", "applied", "upfront", "charge", "detail", "issuer", "method", "number", "period", "region", "status", "billed", "offer", "order", "price", "model", "class", "start", "terms", "grain", "issue", "meter", "zone", "cost", "date", "last", "list", "name", "host", "type", "unit", "tags", "end", "due", "sku", "sub", "id"].sort((a, b) => b.length - a.length);

function splitWords(text: string): string | null {
  const words: string[] = [];
  let rest = text;
  while (rest) {
    const word = FOCUS_WORDS.find(candidate => rest.startsWith(candidate));
    if (!word) return null;
    words.push(word === "id" || word === "sku" ? word.toUpperCase() : word);
    rest = rest.slice(word.length);
  }
  return words.length > 1 ? words.join(" ") : null;
}

export const AGGREGATION_LABELS: Record<PivotAggregation, string> = {
  SUM: "Soma",
  COUNT: "Quantidade de registros",
  COUNT_DISTINCT: "Quantidade de valores distintos",
  AVG: "Média",
  MIN: "Menor valor",
  MAX: "Maior valor",
};

export const OPERATOR_LABELS: Record<PivotFilterOperator, string> = {
  IN: "é igual a",
  NOT_IN: "não é",
  CONTAINS: "contém",
  NOT_CONTAINS: "não contém",
  IS_NULL: "está vazio",
  IS_NOT_NULL: "está preenchido",
};

export function fieldLabel(name: string): string {
  const lower = (name || "").toLowerCase();
  if (FIELD_LABELS[lower]) return FIELD_LABELS[lower];
  const hierarchy = /^hierarquia_(\d+)$/.exec(lower);
  if (hierarchy) return `Hierarquia ${hierarchy[1]}`;
  const text = splitWords(lower) || lower.replace(/^_+/, "").replace(/[_.]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : name;
}

export function friendlyMeasureLabel(measure: PivotMeasure): string {
  // The API echoes technical labels such as "SUM realizado"; only keep labels a person wrote.
  if (measure.label && !/^(SUM|COUNT|COUNT_DISTINCT|AVG|MIN|MAX)\b/i.test(measure.label)) return measure.label;
  if (measure.aggregation === "COUNT") return AGGREGATION_LABELS.COUNT;
  return `${AGGREGATION_LABELS[measure.aggregation] || measure.aggregation} de ${fieldLabel(measure.column)}`;
}
