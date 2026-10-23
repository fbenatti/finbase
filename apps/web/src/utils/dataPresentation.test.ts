import { describe, expect, it } from "vitest";
import { DATA_LAYER_LABELS, DATA_SERVICE_LABELS, dataDisplayText, moduleDisplayName } from "./dataPresentation";

describe("data presentation boundaries", () => {
  it("translates operational messages returned by services", () => {
    const message = dataDisplayText("Trino indisponível. Publicando Silver no Iceberg; backup PostgreSQL, Bronze e Gold. Data Lakehouse, Nessie e MinIO.");
    expect(message).not.toMatch(/trino|silver|iceberg|postgres|bronze|gold|lakehouse|nessie|minio/i);
    expect(message).toContain("serviço de consultas");
    expect(message).toContain("base preparada");
    expect(message).toContain("banco de dados");
  });

  it("preserves task codes and physical identifiers embedded in feedback", () => {
    const identifiers = "gold_reprocess bronze_audit raw_iceberg_costs dest_billing_cloud source_record_id";
    expect(dataDisplayText(identifiers)).toBe(identifiers);
  });

  it("keeps labels separate from backup and permission identifiers", () => {
    expect(Object.keys(DATA_LAYER_LABELS)).toEqual(["bronze", "silver", "gold"]);
    expect(DATA_LAYER_LABELS.silver).toBe("Base preparada");
    expect(moduleDisplayName("gold")).toBe("Dados consolidados");
    expect(moduleDisplayName("etl")).toBe("Operação de dados");
  });

  it("handles pending messages and translates historical namespaces for display", () => {
    expect(dataDisplayText(null)).toBe("");
    expect(dataDisplayText("Origem: iceberg.silver")).toBe("Origem: Base preparada");
    expect(dataDisplayText("Falha no Treno")).toBe("Falha no serviço de consultas");
  });

  it("hides customer and infrastructure clues in user-facing messages", () => {
    const message = dataDisplayText("Falha no Iceberg para 4P (ibm-ti / dimon): Vivo TLF Telefónica; PostgreSQL e Trino indisponíveis.");
    expect(message).not.toMatch(/iceberg|4p|ibm-ti|dimon|vivo|tlf|telef[oó]nica|postgres|trino/i);
    expect(message).toContain("ambiente configurado");
    expect(message).toContain("serviço de consultas");
    expect(message).toContain("banco de dados");
  });

  it("uses a customer-facing label for the Iceberg catalog health key", () => {
    expect(DATA_SERVICE_LABELS.iceberg_catalog).toBe("Catálogo de dados");
  });
});
