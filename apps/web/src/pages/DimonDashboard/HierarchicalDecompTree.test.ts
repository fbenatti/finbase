import { describe, expect, it } from "vitest";
import {
  buildExecutiveDecompChain,
  executiveChainStartIndex,
  isEmptyOrgLevel,
  normalizeDirectorPath,
  pathSegmentFilters,
} from "./HierarchicalDecompTree";

describe("normalizeDirectorPath", () => {
  it("remove Consumo Cloud para alinhar profundidade com departamento_nivel_N", () => {
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
      { column: "departamento_nivel_1", operator: "IN", values: ["Seg"] },
      { column: "departamento_nivel_2", operator: "IN", values: ["Mod"] },
      { column: "departamento_nivel_4", operator: "IN", values: ["Dir"] },
      { column: "departamento_nivel_3", operator: "IN", values: ["Azure"] },
      { column: "departamento_nivel_5", operator: "IN", values: ["Projeto A"] },
    ]);
  });

  it("normaliza prefixo Consumo Cloud antes de mapear colunas", () => {
    const raw = ["Consumo Cloud", "Seg", "Mod", "Dir", "Azure", "Projeto A"];
    expect(pathSegmentFilters(raw, true)).toEqual(pathSegmentFilters(["Seg", "Mod", "Dir", "Azure", "Projeto A"], true));
  });
});

describe("isEmptyOrgLevel", () => {
  it("detecta único filho Não informado em departamento_nivel_6", () => {
    expect(isEmptyOrgLevel("departamento_nivel_6", [{ key: "Não informado" }])).toBe(true);
  });

  it("não trata servicename como nível vazio", () => {
    expect(isEmptyOrgLevel("servicename", [{ key: "Não informado" }])).toBe(false);
  });

  it("não pula quando há filhos distintos", () => {
    expect(isEmptyOrgLevel("departamento_nivel_6", [
      { key: "Não informado" },
      { key: "Unidade A" },
    ])).toBe(false);
  });
});

describe("buildExecutiveDecompChain", () => {
  it("continua em SubProjeto quando escopo cobre até Projeto", () => {
    const start = executiveChainStartIndex(["Seg", "Mod", "Dir", "Azure", "Projeto A"], true);
    expect(start).toBe(5);
    const chain = buildExecutiveDecompChain(true, start);
    expect(chain[0]).toEqual({ field: "departamento_nivel_6", label: "SubProjeto" });
    expect(chain.map(c => c.field)).toEqual([
      "departamento_nivel_6",
      "servicename",
      "servicecategory",
      "servicesubcategory",
      "skumeter",
    ]);
  });

  it("não inclui cloud_origem na cadeia executiva", () => {
    const chain = buildExecutiveDecompChain(true, 0);
    expect(chain.some(c => c.field === "cloud_origem")).toBe(false);
    expect(chain[0].field).toBe("departamento_nivel_1");
  });

  it("após Área (nivel_5) próximo nível é Unidade e depois Serviço", () => {
    const chain = buildExecutiveDecompChain(false, 5);
    expect(chain[0]).toEqual({ field: "departamento_nivel_6", label: "Unidade" });
    expect(chain[1]).toEqual({ field: "servicename", label: "Serviço" });
  });

  it("escopo Consumo Cloud > Azure começa em Plataforma (nivel_3)", () => {
    const path = ["Consumo Cloud", "Azure"];
    expect(executiveChainStartIndex(path, false)).toBe(2);
    const chain = buildExecutiveDecompChain(false, 2);
    expect(chain[0]).toEqual({ field: "departamento_nivel_3", label: "Plataforma" });
  });
});
