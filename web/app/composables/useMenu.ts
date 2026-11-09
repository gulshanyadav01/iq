import { computed, ref } from 'vue'
import { useUserStore } from '~/stores/user'
import { useRoute } from 'vue-router'
import type { MenuItem, MenuSection, MenuConfig, UserRole } from '~/types/menu'
import {
  Layout,
  Settings,
  FileText,
  Bot,
  Zap,
  Upload
} from 'lucide-vue-next'

export const useMenu = () => {
  const userStore = useUserStore()
  const route = useRoute()

  // Mock user roles and permissions - replace with actual role management
  const userRoles = ref<UserRole[]>([
    { id: 'admin', name: 'Administrator', permissions: ['*'] },
    { id: 'user', name: 'User', permissions: ['read', 'write'] },
    { id: 'viewer', name: 'Viewer', permissions: ['read'] }
  ])

  // Current user role (mock - replace with actual user role from store/API)
  const currentUserRole = ref<string>('admin')

  // Default menu configuration
  const defaultMenuConfig = ref<MenuConfig>({
    sections: [
      {
        id: 'main',
        label: 'Main',
        items: [
          {
            id: 'policy-extract',
            label: 'Policy Intake',
            path: '/policy-extract',
            icon: Upload,
            requiresAuth: true,
            roles: ['admin', 'user']
          }
        ]
      },
      {
        id: 'bots',
        label: 'AI Bots',
        isVisible: false,
        items: [
          {
            id: 'crews',
            label: 'Crews',
            path: '/crews',
            icon: Bot,
            requiresAuth: true
          },
          {
            id: 'agents',
            label: 'Agents',
            path: '/agents',
            icon: Zap,
            requiresAuth: true
          },
          {
            id: 'tasks',
            label: 'Tasks',
            path: '/tasks',
            icon: FileText,
            requiresAuth: true
          }
        ]
      },
      {
        id: 'development',
        label: 'Development',
        isVisible: false,
        items: [
          {
            id: 'ui-reference',
            label: 'UI Reference',
            path: '/ui-reference',
            icon: Layout,
            requiresAuth: false
          },
          {
            id: 'simple-demo',
            label: 'Simple Demo',
            path: '/simple',
            icon: Settings,
            requiresAuth: false,
            badge: 'New'
          }
        ]
      }
    ]
  })

  // Custom menu configuration (can be overridden)
  const menuConfig = ref<MenuConfig>(defaultMenuConfig.value)

  // Check if user has required role
  const hasRole = (requiredRoles?: string[]): boolean => {
    if (!requiredRoles || requiredRoles.length === 0) return true
    return requiredRoles.includes(currentUserRole.value)
  }

  // Check if user has required permission
  const hasPermission = (requiredPermissions?: string[]): boolean => {
    if (!requiredPermissions || requiredPermissions.length === 0) return true

    const userRole = userRoles.value.find(role => role.id === currentUserRole.value)
    if (!userRole) return false

    // Check for wildcard permission
    if (userRole.permissions.includes('*')) return true

    // Check specific permissions
    return requiredPermissions.some(permission =>
      userRole.permissions.includes(permission)
    )
  }

  // Check if menu item should be visible
  const isMenuItemVisible = (item: MenuItem): boolean => {
    // Check custom visibility function
    if (typeof item.isVisible === 'function') {
      return item.isVisible()
    } else if (typeof item.isVisible === 'boolean') {
      return item.isVisible
    }

    // Check authentication requirement
    if (item.requiresAuth && !userStore.isLoggedIn) {
      return false
    }

    // Check role requirements
    if (!hasRole(item.roles)) {
      return false
    }

    // Check permission requirements
    if (!hasPermission(item.permissions)) {
      return false
    }

    return true
  }

  // Check if section should be visible
  const isSectionVisible = (section: MenuSection): boolean => {
    // Check custom visibility function
    if (typeof section.isVisible === 'function') {
      return section.isVisible()
    } else if (typeof section.isVisible === 'boolean') {
      return section.isVisible
    }

    // Check authentication requirement
    if (section.requiresAuth && !userStore.isLoggedIn) {
      return false
    }

    // Check role requirements
    if (!hasRole(section.roles)) {
      return false
    }

    // Check permission requirements
    if (!hasPermission(section.permissions)) {
      return false
    }

    // Check if section has any visible items
    return section.items.some(item => isMenuItemVisible(item))
  }

  // Get filtered menu sections
  const visibleSections = computed(() => {
    return menuConfig.value.sections
      .filter(section => isSectionVisible(section))
      .map(section => ({
        ...section,
        items: section.items.filter(item => isMenuItemVisible(item))
      }))
  })

  // Get all visible menu items (flattened)
  const visibleMenuItems = computed(() => {
    return visibleSections.value.flatMap(section => section.items)
  })

  // Check if current route matches menu item
  const isActiveMenuItem = (item: MenuItem): boolean => {
    if (!item.path) return false

    if (item.path === route.path) return true

    // Check if current route starts with menu item path (for nested routes)
    if (route.path.startsWith(item.path) && item.path !== '/') {
      return true
    }

    return false
  }

  // Get active menu item
  const activeMenuItem = computed(() => {
    return visibleMenuItems.value.find(item => isActiveMenuItem(item))
  })

  // Update menu configuration
  const updateMenuConfig = (newConfig: MenuConfig) => {
    menuConfig.value = newConfig
  }

  // Add menu item to specific section
  const addMenuItem = (sectionId: string, item: MenuItem) => {
    const section = menuConfig.value.sections.find(s => s.id === sectionId)
    if (section) {
      section.items.push(item)
    }
  }

  // Remove menu item
  const removeMenuItem = (itemId: string) => {
    menuConfig.value.sections.forEach(section => {
      section.items = section.items.filter(item => item.id !== itemId)
    })
  }

  // Update user role
  const updateUserRole = (roleId: string) => {
    currentUserRole.value = roleId
  }

  return {
    // State
    menuConfig,
    visibleSections,
    visibleMenuItems,
    activeMenuItem,
    currentUserRole,
    userRoles,

    // Methods
    isMenuItemVisible,
    isSectionVisible,
    isActiveMenuItem,
    hasRole,
    hasPermission,
    updateMenuConfig,
    addMenuItem,
    removeMenuItem,
    updateUserRole
  }
}
