#!/bin/sh
set -e

# ============================================================================
# Runtime Configuration
# ============================================================================
# Nuxt automatically reads NUXT_PUBLIC_* environment variables at startup
# No manual config generation needed!

echo "✅ Starting Operator Web Service"
echo "  Runtime config will be loaded from environment variables:"
echo "  - NUXT_PUBLIC_APP_BASE_URL=${NUXT_PUBLIC_APP_BASE_URL:-http://localhost:3000}"
echo "  - NUXT_PUBLIC_API_SCHEME=${NUXT_PUBLIC_API_SCHEME:-http}"
echo "  - NUXT_PUBLIC_API_BASE_URL=${NUXT_PUBLIC_API_BASE_URL:-localhost:8000}"
echo "  - NUXT_PUBLIC_AUTH_BASE_URL=${NUXT_PUBLIC_AUTH_BASE_URL:-http://localhost:10000}"
echo "  - NUXT_PUBLIC_DEFAULT_PROFILE_URL=${NUXT_PUBLIC_DEFAULT_PROFILE_URL:-}"
echo ""
                                                                                                
# Print success message with ASCII art
echo "--------------------------------------------------"
echo " █████╗ ██████╗ ██████╗    ████████╗███████╗███╗   ███╗██████╗ ██╗      █████╗ ████████╗███████╗"
echo "██╔══██╗██╔══██╗██╔══██╗   ╚══██╔══╝██╔════╝████╗ ████║██╔══██╗██║     ██╔══██╗╚══██╔══╝██╔════╝"
echo "███████║██████╔╝██████╔╝█████╗██║   █████╗  ██╔████╔██║██████╔╝██║     ███████║   ██║   █████╗  "
echo "██╔══██║██╔═══╝ ██╔═══╝ ╚════╝██║   ██╔══╝  ██║╚██╔╝██║██╔═══╝ ██║     ██╔══██║   ██║   ██╔══╝  "
echo "██║  ██║██║     ██║           ██║   ███████╗██║ ╚═╝ ██║██║     ███████╗██║  ██║   ██║   ███████╗"
echo "╚═╝  ╚═╝╚═╝     ╚═╝           ╚═╝   ╚══════╝╚═╝     ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚══════╝"
echo ""
echo "✅ Server is running and accessible on:"
echo "   http://localhost:8080"
echo ""
echo "ℹ️ Press CTRL+C to stop the server"


# Start Nitro server (runs in foreground)
node .output/server/index.mjs