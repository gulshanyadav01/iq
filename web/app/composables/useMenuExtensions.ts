import { computed, ref, watch } from 'vue'
import { useMenu } from './useMenu'
import { useUserStore } from '~/stores/user'
import type { MenuItem, MenuSection } from '~/types/menu'
import { 
  Bell, 
  MessageSquare, 
  Calendar, 
  FileText, 
  Settings,
  Download,
  Upload
} from 'lucide-vue-next'

/**
 * Extended menu functionality with dynamic features
 * This composable demonstrates how to extend the base menu system
 */
export const useMenuExtensions = () => {
  const { 
    menuConfig, 
    updateMenuConfig, 
    addMenuItem, 
    removeMenuItem,
    currentUserRole 
  } = useMenu()
  const userStore = useUserStore()

  // Reactive notification count
  const notificationCount = ref(0)
  const unreadMessages = ref(0)
  const pendingTasks = ref(0)

  // Simulate real-time updates
  const simulateNotifications = () => {
    setInterval(() => {
      if (Math.random() > 0.7) {
        notificationCount.value++
        updateNotificationBadges()
      }
    }, 5000)

    setInterval(() => {
      if (Math.random() > 0.8) {
        unreadMessages.value++
        updateMessageBadges()
      }
    }, 7000)
  }

  // Update notification badges dynamically
  const updateNotificationBadges = () => {
    const config = { ...menuConfig.value }
    config.sections.forEach(section => {
      const notificationItem = section.items.find(item => item.id === 'notifications')
      if (notificationItem && notificationCount.value > 0) {
        notificationItem.badge = notificationCount.value.toString()
      }
    })
    updateMenuConfig(config)
  }

  // Update message badges dynamically
  const updateMessageBadges = () => {
    const config = { ...menuConfig.value }
    config.sections.forEach(section => {
      const messageItem = section.items.find(item => item.id === 'messages')
      if (messageItem && unreadMessages.value > 0) {
        messageItem.badge = unreadMessages.value.toString()
      }
    })
    updateMenuConfig(config)
  }

  // Add notification-based menu items
  const addNotificationMenuItems = () => {
    const notificationSection: MenuSection = {
      id: 'notifications',
      label: 'Notifications',
      requiresAuth: true,
      items: [
        {
          id: 'notifications',
          label: 'Notifications',
          path: '/notifications',
          icon: Bell,
          requiresAuth: true,
          badge: notificationCount.value > 0 ? notificationCount.value.toString() : undefined
        },
        {
          id: 'messages',
          label: 'Messages',
          path: '/messages',
          icon: MessageSquare,
          requiresAuth: true,
          badge: unreadMessages.value > 0 ? unreadMessages.value.toString() : undefined
        }
      ]
    }

    const config = { ...menuConfig.value }
    config.sections.unshift(notificationSection)
    updateMenuConfig(config)
  }

  // Add contextual menu items based on current page/state
  const addContextualMenuItems = (context: string) => {
    const contextualItems: Record<string, MenuItem[]> = {
      'project': [
        {
          id: 'export-project',
          label: 'Export Project',
          icon: Download,
          onClick: () => {
            console.log('Exporting project...')
            // Add export logic here
          }
        },
        {
          id: 'import-data',
          label: 'Import Data',
          icon: Upload,
          onClick: () => {
            console.log('Importing data...')
            // Add import logic here
          }
        }
      ],
      'calendar': [
        {
          id: 'new-event',
          label: 'New Event',
          icon: Calendar,
          onClick: () => {
            console.log('Creating new event...')
            // Add event creation logic here
          }
        }
      ]
    }

    const items = contextualItems[context]
    if (items) {
      items.forEach(item => {
        addMenuItem('main', item)
      })
    }
  }

  // Add admin-only menu items
  const addAdminMenuItems = () => {
    if (currentUserRole.value === 'admin') {
      const adminItems: MenuItem[] = [
        {
          id: 'system-logs',
          label: 'System Logs',
          path: '/admin/logs',
          icon: FileText,
          requiresAuth: true,
          roles: ['admin']
        },
        {
          id: 'user-management',
          label: 'User Management',
          path: '/admin/users',
          icon: Settings,
          requiresAuth: true,
          roles: ['admin']
        }
      ]

      adminItems.forEach(item => {
        addMenuItem('system', item)
      })
    }
  }

  // Remove temporary menu items
  const removeTemporaryItems = () => {
    const temporaryIds = ['export-project', 'import-data', 'new-event']
    temporaryIds.forEach(id => {
      removeMenuItem(id)
    })
  }

  // Watch for user role changes and update menu accordingly
  watch(currentUserRole, (newRole) => {
    if (newRole === 'admin') {
      addAdminMenuItems()
    } else {
      // Remove admin items if user is no longer admin
      removeMenuItem('system-logs')
      removeMenuItem('user-management')
    }
  })

  // Watch for authentication status changes
  watch(() => userStore.isLoggedIn, (isLoggedIn) => {
    if (isLoggedIn) {
      addNotificationMenuItems()
      simulateNotifications()
    } else {
      // Clean up notification items when logged out
      const config = { ...menuConfig.value }
      config.sections = config.sections.filter(section => section.id !== 'notifications')
      updateMenuConfig(config)
    }
  })

  // Toggle menu section visibility
  const toggleSection = (sectionId: string) => {
    const config = { ...menuConfig.value }
    const section = config.sections.find(s => s.id === sectionId)
    if (section) {
      section.isVisible = typeof section.isVisible === 'boolean' ? !section.isVisible : false
      updateMenuConfig(config)
    }
  }

  // Get menu statistics
  const menuStats = computed(() => {
    const allItems = menuConfig.value.sections.flatMap(section => section.items)
    return {
      totalSections: menuConfig.value.sections.length,
      totalItems: allItems.length,
      authRequiredItems: allItems.filter(item => item.requiresAuth).length,
      adminOnlyItems: allItems.filter(item => item.roles?.includes('admin')).length,
      itemsWithBadges: allItems.filter(item => item.badge).length
    }
  })

  return {
    // State
    notificationCount,
    unreadMessages,
    pendingTasks,
    menuStats,

    // Methods
    addNotificationMenuItems,
    addContextualMenuItems,
    addAdminMenuItems,
    removeTemporaryItems,
    toggleSection,
    simulateNotifications,
    updateNotificationBadges,
    updateMessageBadges
  }
}
