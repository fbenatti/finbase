import { describe, expect, it, vi, beforeEach } from "vitest";

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();

vi.mock("./api", () => ({
  api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), delete: (...args: unknown[]) => del(...args) },
}));

import { createRoleAcl, grantUserScope, listRoleAcls, simulateDecision } from "./accessApi";

describe("accessApi", () => {
  beforeEach(() => {
    get.mockReset().mockResolvedValue({ data: { allowed: true, reason: "ok" } });
    post.mockReset().mockResolvedValue({ data: { id: 1 } });
    del.mockReset().mockResolvedValue({ data: null });
  });

  it("simulateDecision chama o simulador da pessoa com os parâmetros", async () => {
    const result = await simulateDecision(10, { operation: "read", resource_type: "table", resource: "fact_cost" });
    expect(get).toHaveBeenCalledWith("/access/users/10/decision", {
      params: { operation: "read", resource_type: "table", resource: "fact_cost" },
    });
    expect(result.allowed).toBe(true);
  });

  it("createRoleAcl envia o corpo da ACL para o perfil", async () => {
    await createRoleAcl(3, {
      resource_type: "table", resource: "fact_cost", operation: "read", effect: "allow",
      conditions: [{ column: "department_id", scope_type: "hierarchy", match_mode: "subtree" }],
    });
    expect(post).toHaveBeenCalledWith("/access/roles/3/acls", expect.objectContaining({ resource: "fact_cost", effect: "allow" }));
  });

  it("listRoleAcls lê as ACLs do perfil", async () => {
    await listRoleAcls(3);
    expect(get).toHaveBeenCalledWith("/access/roles/3/acls");
  });

  it("grantUserScope usa o motivo e o alcance informados", async () => {
    await grantUserScope(10, { scope_id: 2, match_mode: "subtree", reason: "projeto TI" });
    expect(post).toHaveBeenCalledWith("/access/users/10/scopes", { scope_id: 2, match_mode: "subtree", reason: "projeto TI" });
  });
});
