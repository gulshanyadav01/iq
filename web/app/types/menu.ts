import type { Component } from 'vue'

export interface MenuItem {
  id: string
  label: string
  path?: string
  icon?: Component | string
  badge?: string | number
  children?: MenuItem[]
  isVisible?: boolean | (() => boolean)
  requiresAuth?: boolean
  roles?: string[]
  permissions?: string[]
  isExternal?: boolean
  onClick?: () => void
  divider?: boolean
  section?: string
}

export interface MenuSection {
  id: string
  label?: string
  items: MenuItem[]
  isVisible?: boolean | (() => boolean)
  requiresAuth?: boolean
  roles?: string[]
  permissions?: string[]
}

export interface MenuConfig {
  sections: MenuSection[]
}

export interface UserRole {
  id: string
  name: string
  permissions: string[]
}
