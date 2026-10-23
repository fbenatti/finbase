import { describe, expect, it } from "vitest";
import {
  buildExecutiveDecompChain,
  executiveChainStartIndex,
  isEmptyOrgLevel,
  normalizeDirectorPath,
  pathSegmentFilters,
} from "./HierarchicalDecompTree";

describe("normalizeDirectorPath", () => {
  it("remove Consumo Cloud para alinhar profundidade com hierarquia_N", () => {
    const raw = ["Consumo Cloud", "Seg", "Mod", "Dir", "Azure", "Projeto A"];
    expect(normalizeDirectorPath(raw, true)).toEqual(["Seg", "Mod", "Dir", "Azure", "Projeto A"]);
  });

  it("retorna vazio quando só a raiz Consumo Cloud está selecionada", () => {
    expect(normalizeDirectorPath(["Consumo Cloud"], true)).toEqual([]);
  });

  it("não altera caminhos fora da visão diretoria", () => {
    const path = ["Consumo Cloud", "Azure"];
    expect(normalizeDirectorPath(path, false)).toEqual(path);
  });
});

describe("pathSegmentFilters (visão diretoria)", () => {
  it("mapeia Segmento→Modelo→Diretoria→Cloud→Projeto para níveis 1,2,4,3,5", () => {
    const path = ["Seg", "Mod", "Dir", "Azure", "Projeto A"];
    expect(pathSegmentFilters(path, true)).toEqual([
      { column: "hierarquia_1", operator: "IN", values: ["Seg"] },
      { column: "hierarquia_2", operator: "IN", values: ["Mod"] },
      { column: "hierarquia_4", operator: "IN", values: ["Dir"] },
      { column: "hierarquia_3", operator: "IN", values: ["Azure"] },
      { column: "hierarquia_5", operator: "IN", values: ["Projeto A"] },
    ]);
  });

  it("normaliza prefixo Consumo Cloud antes de mapear colunas", () => {
    const raw = ["Consumo Cloud", "Seg", "Mod", "Dir", "Azure", "Projeto A"];
    expect(pathSegmentFilters(raw, true)).toEqual(pathSegmentFilters(["Seg", "Mod", "Dir", "Azure", "Projeto A"], true));
  });
});

describe("isEmptyOrgLevel", () => {
  it("detecta único filho Não informado em hierarquia_6", () => {
    expect(isEmptyOrgLevel("hierarquia_6", [{ key: "Não informado" }])).toBe(true);
  });

  it("não trata servicename como nível vazio", () => {
    expect(isEmptyOrgLevel("servicename", [{ key: "Não informado" }])).toBe(false);
  });

  it("não pula quando há filhos distintos", () => {
    expect(isEmptyOrgLevel("hierarquia_6", [
      { key: "Não informado" },
      { key: "Unidade A" },
    ])).toBe(false);
  });
});

describe("buildExecutiveDecompChain", () => {
  it("continua no nível 6 quando escopo cobre até Projeto", () => {
    const start = executiveChainStartIndex(["Seg", "Mod", "Dir", "Azure", "Projeto A"], true);
    expect(start).toBe(5);
    const chain = buildExecutiveDecompChain(true, start);
    expect(chain[0]).toEqual({ field: "hierarquia_6", label: "Subprojeto" });
    expect(chain.map(c => c.field)).toEqual([
      "hierarquia_6",
      "servicename",
      "servicesubcategory",
      "skumeter",
    ]);
  });

  it("não inclui cloud_origem na cadeia executiva", () => {
    const chain = buildExecutiveDecompChain(true, 0);
    expect(chain.some(c => c.field === "cloud_origem")).toBe(false);
    expect(chain[0].field).toBe("hierarquia_1");
  });

  it("após o nível 5 o próximo é o nível 6 e depois Serviço", () => {
    const chain = buildExecutiveDecompChain(false, 5);
    expect(chain[0]).toEqual({ field: "hierarquia_6", label: "Subprojeto" });
    expect(chain[1]).toEqual({ field: "servicename", label: "Serviço" });
  });

  it("escopo Consumo Cloud > Azure começa no nivel_3", () => {
    const path = ["Consumo Cloud", "Azure"];
    expect(executiveChainStartIndex(path, false)).toBe(2);
    const chain = buildExecutiveDecompChain(false, 2);
    expect(chain[0]).toEqual({ field: "hierarquia_3", label: "Provedor" });
  });

  it("usa os rótulos do escopo quando informados", () => {
    const labelOf = (field: string) => (field === "hierarquia_4" ? "Diretoria" : field);
    const chain = buildExecutiveDecompChain(false, 3, labelOf);
    expect(chain[0]).toEqual({ field: "hierarquia_4", label: "Diretoria" });
  });
});
