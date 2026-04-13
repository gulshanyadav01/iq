import {createAuthClient} from "better-auth/client";
import { anonymous, bearer, jwt } from "better-auth/plugins";
import { useRuntimeConfig } from "nuxt/app";
import { useUserStore } from "../stores/user";

export function useAuthClient() {
  const config = useRuntimeConfig();
  const {signIn, signUp, signOut, getSession, token} = createAuthClient({
    baseURL: config.public.authBaseUrl as string,
    advanced: {
      cookiePrefix: "rhobots"
    },
    plugins: [
      anonymous(),
      jwt(),
      bearer({
        requireSignature: true
      })
    ]
  });

  const setJwtToken = async () => {
    const {data, error} = await token()
    if (error) throw error
    if (data?.token) {
      const userStore = useUserStore()
      userStore.setToken(data?.token)
    }
    return data?.token
  }

  return {
    signIn,
    signUp,
    signOut,
    getSession,
    setJwtToken
  }
}