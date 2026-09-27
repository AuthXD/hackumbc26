# TeachBack landing

Public site for [teachonce.study](https://teachonce.study). It is a separate Vite app. It does not start the TeachBack camera, API, or database.

## Local

```bash
cd landing
npm install
npm run dev
```

```bash
npm run typecheck
npm run build
```

The production files are written to `landing/dist`.

Optional public links live in `.env` (see `.env.example`). A blank value hides that button. Do not commit `.env`.

## Vercel

| Setting | Value |
|---|---|
| Root Directory | `landing` |
| Build Command | `npm run build` |
| Output Directory | `dist` |

Set `VITE_DEMO_VIDEO_URL`, `VITE_LIVE_DEMO_URL`, and `VITE_GITHUB_URL` in the Vercel project if those links should appear. They are public URLs, not secrets.
