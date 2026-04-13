import { useRuntimeConfig } from "nuxt/app";

export function useBaseUrl() {
  const config = useRuntimeConfig()

  const getBaseUrl = () => {
    let baseUrl = config.public.apiBaseUrl
    return `${config.public.apiScheme}://${baseUrl}`
  }

  return {
    getBaseUrl
  }
}