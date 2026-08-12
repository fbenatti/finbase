# OpenFinops Web — Frontend (React + Vite)

React/TypeScript frontend for managing FinOps data pipelines, rules, and analysis.

**Dev port:** 5173 (proxies `/api` → backend on `:8000`)

## Directory Structure

```
apps/web/
├── Dockerfile
├── vite.config.ts              # Vite config (React plugin, aliases, API proxy)
├── tsconfig.json
├── index.html                  # HTML entry point
│
└── src/
    ├── main.tsx                 # React entry point
    ├── App.tsx                  # Root component + React Router routes
    ├── index.css                # Global styles
    │
    ├── layout/
    │   └── Layout.tsx           # App shell (sidebar navigation + header)
    │
    ├── pages/                   # 12 page components
    │   ├── Analysis/Analysis.tsx         # Trino SQL editor + Iceberg table explorer
    │   ├── Classification/Classification.tsx  # Classification rules management
    │   ├── Connections/Connections.tsx   # Cloud connection CRUD
    │   ├── Departments/Departments.tsx   # Department hierarchy + CSV import
    │   ├── Destinations/Destinations.tsx # Destination tables schema builder
    │   ├── Explorer/Explorer.tsx         # Source & destination data browser
    │   ├── Extraction/Extraction.tsx     # ETL scan + run + schedules
    │   ├── Mappings/Mappings.tsx         # ETL column mapping management
    │   ├── Pipeline/PipelineBuilder.tsx  # Visual pipeline (React Flow)
    │   ├── Relationship/Relationship.tsx # Source→destination drag-and-drop mapping
    │   ├── Reprocess/Reprocess.tsx       # Destination table reprocessing + progress
    │   └── Rules/Rules.tsx               # Business rules (expressions + conditions)
    │
    ├── components/              # Shared UI components
    │   ├── EmptyState.tsx       # Reusable empty state placeholder
    │   └── SortableTable.tsx    # Reusable sortable/filterable data table
    │
    ├── services/
    │   └── api.ts               # All API client functions + TypeScript interfaces
    │
    └── theme/
        └── theme.ts             # Material UI theme configuration
```

## Page Routes

| Route              | Page                    | Description                                       |
|--------------------|-------------------------|---------------------------------------------------|
| `/`                | Connections             | Manage cloud provider connections                 |
| `/extraction`      | Extraction              | Scan blobs, run ETL, view/cancel tasks, schedules  |
| `/rules`           | Rules                   | Business rules (expressions, conditions, formulas) |
| `/classification`  | Classification          | Classification rules + department mapping          |
| `/departments`     | Departments             | Hierarchical department/project tree               |
| `/mappings`        | Mappings                | ETL column mappings                                |
| `/relationship`    | Relationship            | Drag-and-drop source→destination column mapping    |
| `/destinations`    | Destinations            | Destination table schema management                |
| `/pipeline`        | Pipeline Builder        | Visual pipeline flow diagram (React Flow)           |
| `/reprocess`       | Reprocess               | Destination table reprocessing with progress bar   |
| `/analysis`        | Analysis                | Trino SQL queries + data exploration               |
| `/explorer`        | Explorer                | Browse source and destination data                 |

## API Client (`services/api.ts`)

Centralized API client using **axios**. Defines all TypeScript interfaces and ~60 async functions organized by domain:

- **Connections:** `getConnections`, `createConnection`, `updateConnection`, `deleteConnection`, `testConnection`
- **ETL/Blobs:** `scanBlobs`, `runEtl`, `getTaskStatus`, `cancelEtl`, `skipBlob`, `unskipBlob`
- **Reprocess:** `reprocessDest`, `getReprocessStatus`, `cancelReprocess`, `getAvailablePeriods`
- **Business Rules:** `getBusinessRules`, `createBusinessRule`, `updateBusinessRule`, `deleteBusinessRule`
- **Classifications:** `getClassifications`, `createClassification`, `updateClassification`, `deleteClassification`
- **Departments/Projects:** Full CRUD + `importCsvData`
- **Destinations:** Full CRUD for tables, columns, and mappings
- **Analysis:** `runAnalysisQuery`, `getAnalysisDestinations`, `queryAnalysisDestination`
- **Schedules:** `getSchedules`, `createSchedule`, `updateSchedule`, `deleteSchedule`, `runScheduleNow`

## Technology Stack

| Technology      | Purpose                              |
|-----------------|--------------------------------------|
| React 18        | UI framework                         |
| TypeScript      | Type safety                          |
| Vite 6          | Build tool / dev server              |
| Material UI     | Component library                    |
| React Router    | Client-side routing                  |
| Axios           | HTTP client                          |
| React Flow      | Visual pipeline diagram (Pipeline)   |

## Vite Proxy Configuration

The dev server proxies `/api` requests to the FastAPI backend:

```typescript
// vite.config.ts
server: {
  proxy: {
    "/api": {
      target: "http://localhost:8000",
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ""),
    },
  },
}
```

So `GET /api/connections/` from the browser → `GET /connections/` on the backend.
