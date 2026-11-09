// https://nuxt.com/docs/api/configuration/nuxt-config
import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({

  compatibilityDate: '2025-07-15',
  // SPA in dev (SSR in prod). This box's disk is slow enough that vite-node's
  // server-side module fetch exceeds its 10s timeout and wedges the dev server
  // ("Request timeout ... for type: module"). Skipping dev SSR avoids that path
  // entirely; the app is an auth-gated client-rendered dashboard, so no SSR loss.
  ssr: process.env.NODE_ENV === 'production',
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],
  vite: {
    plugins: [
      tailwindcss(),
    ],
    server: {
      allowedHosts: true,
    },
  },
  modules: [
    '@nuxt/eslint',
    '@nuxt/image',
    '@nuxt/scripts',
    '@nuxt/test-utils',
    '@nuxt/ui',
    '@pinia/nuxt'
  ],
  app: {
    baseURL: process.env.NUXT_APP_BASE_URL || '/',
    pageTransition: {name: 'page', mode: 'out-in'}
  },
  runtimeConfig: {
    public: {
      appBaseUrl: process.env.NUXT_PUBLIC_APP_BASE_URL,
      apiScheme: process.env.NUXT_PUBLIC_API_SCHEME,
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL,
      authBaseUrl: process.env.NUXT_PUBLIC_AUTH_BASE_URL,
      enabledSocialProviders: process.env.NUXT_PUBLIC_ENABLED_SOCIAL_PROVIDERS || '',
    },
  }
})
