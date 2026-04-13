---
paths:
  - "web/**"
---

# Frontend Rules

- TypeScript only — no plain `.js` files in `web/app/`
- UI components: use Nuxt UI 3 primitives before writing custom components
- Styling: Tailwind CSS 4 utility classes — no custom CSS unless unavoidable
- State: Pinia stores in `web/app/stores/` — one store per domain
- API calls: `useFetch` or `$fetch` using `useRuntimeConfig().public.apiBaseUrl`
- Auth: use the Better Auth composable — never call auth endpoints directly
- Routes: Nuxt file-based routing in `web/app/pages/` — no manual router config
- Icons: Lucide Vue (`lucide-vue-next`) — don't add other icon libraries
- Run lint: `cd web && pnpm lint`
- Run dev (frontend only): `cd web && pnpm dev`

## Design System

- **Font scale**: base 13px (`html { font-size: 13px }`). Page headings `text-xl font-semibold tracking-tight`, section headings `text-sm font-semibold`, body `text-sm`, meta/captions `text-xs text-gray-400`, sidebar items `text-xs`, section labels `text-[10px] uppercase tracking-widest`
- **Glass utilities**: `.glass` and `.glass-subtle` are defined in `main.css` — use them instead of re-inlining `backdrop-filter`/`bg-white/N` values
- **Sidebar**: floating glass panel (inset `m-3`, `rounded-2xl`). Never use opaque gradients in the sidebar.
- **Backgrounds**: layout wrappers apply a noise texture (`.noise` class) + radial gradient hint. Do not add extra background colors on page-level components — let the layout show through.
- **Gradients**: subtle only — max `from-primary-*/10` (light) or `/20` (dark) opacity. Heavy opaque gradients are reserved for the login page only.
- **Spacing**: generous whitespace — page padding `p-8`/`p-10`, section gaps `gap-6`/`gap-8`, avoid cramped `p-4` containers.
- **Animations**: only purposeful transitions on interactive elements (`duration-200`/`300`). No ambient animations (particles, orbs, keyframes) outside the login page.
- **Appbar**: `h-12`, `backdrop-blur-xl`, `bg-white/75 dark:bg-gray-950/75`.
- **Active menu item**: left border accent `border-l-2 border-primary-500` + `bg-primary-500/10`.
- **Inputs**: globally styled in `main.css` — don't override padding, ring color, or border individually on each page.
