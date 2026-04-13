export type SocialProvider = 'google' | 'github' | 'microsoft'

export const providerConfig: Record<SocialProvider, { label: string; icon: string; alt: string }> = {
  google: {
    label: 'Continue with Google',
    icon: 'https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg',
    alt: 'Google'
  },
  github: {
    label: 'Continue with GitHub',
    icon: 'https://www.svgrepo.com/show/475654/github-color.svg',
    alt: 'Github'
  },
  microsoft: {
    label: 'Continue with Microsoft',
    icon: 'https://www.svgrepo.com/show/448239/microsoft.svg',
    alt: 'Microsoft'
  }
}

export function useSocialProviders() {
  const config = useRuntimeConfig()

  const enabledProviders = computed(() => {
    const providers = config.public.enabledSocialProviders as string
    if (!providers) return []
    return providers.split(',').map(p => p.trim()).filter((p): p is SocialProvider =>
      p in providerConfig
    )
  })

  const hasSocialProviders = computed(() => enabledProviders.value.length > 0)

  return {
    enabledProviders,
    hasSocialProviders,
    providerConfig
  }
}
