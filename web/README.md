# ANUMATI web console — [M6]

Not scaffolded yet. M6 creates a Vite + React + TypeScript app here with Tailwind,
shadcn/ui and Recharts:

```
src/pages/       Console.tsx  Checkout.tsx  Ledger.tsx  Disputes.tsx  Metrics.tsx
src/components/  PhoneStepUp.tsx  VerdictCard.tsx
```

Until then `make web` is a no-op and the `web` service in docker-compose.yml is
commented out. The API runs on http://localhost:8000 and already allows CORS from
http://localhost:5173.
