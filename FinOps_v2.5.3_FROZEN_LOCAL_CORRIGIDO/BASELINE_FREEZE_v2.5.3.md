# BASELINE FREEZE - FinOps Intelligence Platform v2.5.3

Status: FROZEN / APPROVED BASELINE
Version: v2.5.3
Codename: Trend Ranking Functional Fix
Parent baseline: v2.4.2 FROZEN
Frozen on: 2026-08-20

## Freeze rules
1. v2.5.3 is the new functional baseline for all subsequent evolution.
2. The HTML frozen artifact must not be overwritten in place.
3. Every future evolution must create a new semantic version.
4. Data contract remains unchanged unless explicitly approved.
5. Regressions must be tested against:
   - CSV load
   - UTF-8/accents
   - dark mode
   - filters
   - Smart Period Selector
   - Executive / Investigation navigation
   - Trend Intelligence buttons
   - Decomposition
   - Data Quality
   - Comparator
   - Heatmap
   - Priority
   - Drill-down
6. Trend Intelligence v2.5.3 rule is authoritative:
   - cost/growth/reduction/volatility = Tipo=realizado only
   - gap trend only where Realizado and Forecast coexist in the same competence.
