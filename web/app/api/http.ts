import {useFetch} from 'nuxt/app'
import {useBaseUrl} from "~/composables/useBaseUrl";

const API_PATH = '/api'

export interface ApiResponse<T> {
  data: T | null
  error: Error | null
  ok?: boolean
  status?: number
}

export class HttpClient {
  private readonly baseUrl: string

  constructor(baseUrl: string = API_PATH) {
    this.baseUrl = baseUrl
  }

  private async getHeaders(isProtected: boolean = true): Promise<Record<string, string>> {
    const {getToken} = useUserStore()
    const token = await getToken()

    if (isProtected && !token) {
      throw {status: 401}
    }

    const headers = {
      'Content-Type': 'application/json',
      'Authorization': ''
    }

    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    return headers
  }

  private getUrl(endpoint: string): string {
    const {getBaseUrl} = useBaseUrl()
    return `${getBaseUrl()}${this.baseUrl}${endpoint}`
  }

  async get<T>(endpoint: string, queryParams?: Record<string, string | number>, isFinalUrl?: boolean, isProtected: boolean = true): Promise<ApiResponse<T>> {
    try {
      const url = isFinalUrl ? endpoint : this.getUrl(endpoint);

      // Append query params if provided
      const finalUrl = queryParams
        ? `${url}?${new URLSearchParams(queryParams as Record<string, string>).toString()}`
        : url;

      const { data, error } = await useFetch<T>(finalUrl, {
        headers: await this.getHeaders(isProtected),
      });

      return {
        data: data.value,
        error: error.value as Error,
      };
    } catch (error) {
      return {
        data: null,
        error: error as Error,
      };
    }
  }


  async patch<T>(endpoint: string, body: any): Promise<ApiResponse<T>> {
    try {
      const {data, error} = await useFetch<T>(this.getUrl(endpoint), {
        method: 'PATCH',
        headers: await this.getHeaders(),
        body: JSON.stringify(body)
      })

      return {
        data: data.value,
        error: error.value as Error
      }
    } catch (error) {
      return {
        data: null,
        error: error as Error
      }
    }
  }

  async post<T>(endpoint: string, body: any): Promise<ApiResponse<T>> {
    try {
      const headers = await this.getHeaders()

      // Remove Content-Type for FormData
      if (body instanceof FormData) {
        delete headers['Content-Type']
      }

      const {data, error} = await useFetch<T>(this.getUrl(endpoint), {
        method: 'POST',
        headers,
        body: body instanceof FormData ? body : JSON.stringify(body)
      })

      return {
        data: data.value,
        error: error.value as Error
      }
    } catch (error) {
      return {
        data: null,
        error: error as Error
      }
    }
  }

  async delete<T>(endpoint: string): Promise<ApiResponse<T>> {
    try {
      const {data, error} = await useFetch<T>(this.getUrl(endpoint), {
        method: 'DELETE',
        headers: await this.getHeaders()
      })

      return {
        data: data.value,
        error: error.value as Error
      }
    } catch (error) {
      return {
        data: null,
        error: error as Error
      }
    }
  }
}