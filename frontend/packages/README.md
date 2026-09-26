# Frontend Workspace Packages

- `@panwatch/api`: the single HTTP entry point and domain APIs (auth, version, stocks, etc.).
- `@panwatch/base-ui`: base UI components and style helpers (moved from `src/components/ui/*`).
- `@panwatch/biz-ui`: business components and shared business logic (moved from the business components in `src/components/*`).

Frontend pages now all make API requests through `@panwatch/api` instead of calling `fetch` directly.
