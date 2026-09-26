# Frontend Workspace Packages

- `@candlewise/api`: the single HTTP entry point and domain APIs (auth, version, stocks, etc.).
- `@candlewise/base-ui`: base UI components and style helpers (moved from `src/components/ui/*`).
- `@candlewise/biz-ui`: business components and shared business logic (moved from the business components in `src/components/*`).

Frontend pages now all make API requests through `@candlewise/api` instead of calling `fetch` directly.
