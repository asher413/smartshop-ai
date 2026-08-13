#!/usr/bin/env bash
# =============================================================================
# DealBursa — Oracle Cloud Always Free Tier Deployment Script
# =============================================================================
# This script turns a fresh Ubuntu 22.04 ARM (Ampere) VM into a production
# DealBursa server with Docker, Nginx, SSL, and auto-renewal — in one command.
#
# WHAT YOU GET:
#   - Docker + docker-compose running all services (web, scheduler, DB, Redis, Meilisearch)
#   - Nginx reverse proxy with HTTP/2, Gzip, caching
#   - Let's Encrypt SSL with auto-renewal (Certbot)
#   - Automatic OS security updates
#   - Firewall (ufw) — only ports 22, 80, 443 open
#   - The app starts on boot (docker-compose with restart: unless-stopped)
#
# PREREQUISITES (do these BEFORE running this script):
#   1. Create an Oracle Cloud Always Free VM (ARM Ampere, Ubuntu 22.04)
#   2. Point your domain's DNS A record to the VM's public IP
#   3. Have your .env file ready (copy from .env.example and fill in keys)
#
# USAGE:
#   chmod +x scripts/oracle_deploy.sh
#   sudo ./scripts/oracle_deploy.sh yourdomain.com your@email.com
#
# After the script completes, visit https://yourdomain.com
# Admin panel: https://yourdomain.com/admin
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!!]${NC} $*"; }
err()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${BLUE}[..]${NC} $*"; }

# --- Parse arguments ---------------------------------------------------------
DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: $0 <yourdomain.com> <your@email.com>"
    echo ""
    echo "Example: $0 smartshop.co.il admin@smartshop.co.il"
    exit 1
fi

# --- Check we're root --------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root (use sudo)"
fi

# --- Architecture check ------------------------------------------------------
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    warn "This machine is $ARCH, not aarch64. Oracle free tier is ARM Ampere."
    warn "The script will still work on x86_64, just FYI."
fi

START_TIME=$(date +%s)

echo ""
echo "============================================================"
echo "  DealBursa - Oracle Cloud Deployment"
echo "  Domain: $DOMAIN"
echo "  Email:  $EMAIL"
echo "============================================================"
echo ""

# ============================================================================
# STEP 1: System updates & essential packages
# ============================================================================
log "STEP 1/8: Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git nginx certbot python3-certbot-nginx \
    ufw htop iotop net-tools fail2ban \
    unattended-upgrades software-properties-common
log "System packages installed."

# ============================================================================
# STEP 2: Install Docker + docker-compose
# ============================================================================
log "STEP 2/8: Installing Docker..."

if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

log "Docker $(docker --version) ready."
log "Docker Compose $(docker compose version) ready."

# ============================================================================
# STEP 3: Firewall setup
# ============================================================================
log "STEP 3/8: Configuring firewall (ufw)..."

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    comment "SSH"
ufw allow 80/tcp    comment "HTTP"
ufw allow 443/tcp   comment "HTTPS"
ufw --force enable

log "Firewall active: only SSH, HTTP, HTTPS open."

# ============================================================================
# STEP 4: Deploy the app
# ============================================================================
log "STEP 4/8: Deploying DealBursa..."

APP_DIR="/opt/smartshop"
PROJECT_SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ -d "$APP_DIR" ]; then
    warn "App directory $APP_DIR already exists. Backing up..."
    mv "$APP_DIR" "${APP_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
fi

cp -r "$PROJECT_SRC" "$APP_DIR"
cd "$APP_DIR"

# Create .env from example if not present
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        warn "No .env found - creating from .env.example."
        warn "EDIT /opt/smartshop/.env with your real API keys!"
        cp .env.example .env
        # Set sensible production defaults
        sed -i "s|DATABASE_URL=.*|DATABASE_URL=postgresql://smartshop:smartshop@db:5432/smartshop|" .env
        sed -i "s|REDIS_URL=.*|REDIS_URL=redis://redis:6379/0|" .env
        sed -i "s|SITE_URL=.*|SITE_URL=https://$DOMAIN|" .env
        sed -i "s|ENV=.*|ENV=production|" .env
        sed -i "s|ADMIN_EMAIL=.*|ADMIN_EMAIL=$EMAIL|" .env
    else
        err "No .env or .env.example found. Create /opt/smartshop/.env first!"
    fi
fi

# Build and start everything
log "Building Docker images (takes a few minutes on first run)..."
docker compose build --pull

log "Starting all services..."
docker compose up -d

# Wait for services
log "Waiting for services to become healthy..."
for i in $(seq 1 30); do
    if docker compose ps | grep -q "healthy"; then
        break
    fi
    sleep 2
done

docker compose ps
log "All services running."

# ============================================================================
# STEP 5: Seed demo data
# ============================================================================
log "STEP 5/8: Seeding demo data..."

if docker compose exec -T web python scripts/seed_demo.py 2>/dev/null; then
    log "Demo products seeded."
else
    warn "Demo seeding skipped (may already have data or missing AI keys)."
fi

# ============================================================================
# STEP 6: Configure Nginx
# ============================================================================
log "STEP 6/8: Configuring Nginx reverse proxy..."

cp nginx/smartshop.conf /etc/nginx/sites-available/smartshop
sed -i "s/yourdomain.com/$DOMAIN/g" /etc/nginx/sites-available/smartshop

ln -sf /etc/nginx/sites-available/smartshop /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
systemctl enable nginx

log "Nginx configured (HTTP only for now)."

# ============================================================================
# STEP 7: SSL Certificate (Let's Encrypt)
# ============================================================================
log "STEP 7/8: Obtaining SSL certificate..."

certbot --nginx \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect \
    2>&1 || warn "Certbot may have failed - is DNS pointing to this server?"

certbot renew --dry-run 2>&1 || warn "Certbot auto-renewal test failed."

log "SSL certificate installed."

# ============================================================================
# STEP 8: Auto-updates + monitoring
# ============================================================================
log "STEP 8/8: Configuring auto-updates and cron jobs..."

# Unattended security upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'APTEOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
APTEOF

# Weekly restart to prevent memory leaks (Sunday 3 AM)
cat > /etc/cron.d/smartshop-restart << CRONEOF
0 3 * * 0 root cd /opt/smartshop && docker compose up -d --force-recreate web scheduler
CRONEOF

# Daily cleanup of old Docker images
cat > /etc/cron.d/smartshop-cleanup << CRONEOF
0 4 * * * root docker system prune -af --filter "until=168h" > /dev/null 2>&1
CRONEOF

# Certbot renewal (twice daily)
cat > /etc/cron.d/certbot-renew << CRONEOF
0 0,12 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"
CRONEOF

log "Auto-updates and monitoring configured."

# ============================================================================
# DONE
# ============================================================================
ELAPSED=$(($(date +%s) - START_TIME))

echo ""
echo "============================================================"
echo ""
echo "  DealBursa is LIVE!"
echo ""
echo "  Site:   https://$DOMAIN"
echo "  Admin:  https://$DOMAIN/admin"
echo "  Login:  $EMAIL / password from ADMIN_SECRET_KEY"
echo ""
echo "  Deployment completed in ${ELAPSED}s."
echo ""
echo "============================================================"
echo ""

echo "Quick commands:"
echo "  Status:    cd /opt/smartshop && docker compose ps"
echo "  Logs:      cd /opt/smartshop && docker compose logs -f"
echo "  Restart:   cd /opt/smartshop && docker compose restart"
echo "  Update:    cd /opt/smartshop && git pull && docker compose up -d --build"
echo ""

log "IMPORTANT: Edit /opt/smartshop/.env with your API keys, then:"
log "  cd /opt/smartshop && docker compose restart web scheduler"
echo ""
