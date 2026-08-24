import asyncio
from app.services.ai_cost_attribution import propagate_allocated_costs

async def main():
    periods = ["202601", "202602", "202603", "202604", "202605", "202606", "202607"]
    for p in periods:
        print(f"Propagating {p}...")
        try:
            res = await propagate_allocated_costs(p, "github", "Copilot Business")
            print(f"Result {p}: {res}")
        except Exception as e:
            print(f"Error {p}: {e}")

asyncio.run(main())
