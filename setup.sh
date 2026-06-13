#!/bin/bash
set -e

echo "=========================================="
echo "  AnimeDekho Bot — EC2 Docker Setup"
echo "=========================================="
echo ""

# ─── Step 1: Install Docker ──────────────────────────────────────
echo "[1/4] Installing Docker..."

# Remove old/broken Docker packages
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
    sudo apt-get remove -y $pkg 2>/dev/null || true
done

# Add Docker's official GPG key and repo
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add current user to docker group
sudo usermod -aG docker $USER 2>/dev/null || true

echo "✅ Docker installed!"
echo ""

# ─── Step 2: Collect Environment Variables ────────────────────────
echo "[2/4] Setting up environment variables..."
echo ""

read -p "Enter API_ID (from my.telegram.org): " API_ID
read -p "Enter API_HASH (from my.telegram.org): " API_HASH
read -p "Enter BOT_TOKEN (from @BotFather): " BOT_TOKEN
read -p "Enter OWNER_ID (your Telegram user ID): " OWNER_ID
read -p "Enter MAIN_CHANNEL (channel ID, e.g. -1001234567890, or press Enter to skip): " MAIN_CHANNEL
read -p "Enter LOG_CHANNEL (channel ID, or press Enter to skip): " LOG_CHANNEL

# MongoDB runs in Docker, so use internal hostname
MONGO_URI="mongodb://mongo:27017/animedekho"

# Write .env file
cat > .env << EOF
API_ID=${API_ID}
API_HASH=${API_HASH}
BOT_TOKEN=${BOT_TOKEN}
OWNER_ID=${OWNER_ID}
MONGO_URI=${MONGO_URI}
MAIN_CHANNEL=${MAIN_CHANNEL:-0}
LOG_CHANNEL=${LOG_CHANNEL:-0}
LOG_LEVEL=INFO
EOF

echo ""
echo "✅ .env file created!"
echo ""

# ─── Step 3: Build Docker Image ──────────────────────────────────
echo "[3/4] Building Docker image..."
docker compose build

echo "✅ Docker image built!"
echo ""

# ─── Step 4: Start the Bot ───────────────────────────────────────
echo "[4/4] Starting the bot..."
docker compose up -d

echo ""
echo "=========================================="
echo "  ✅ AnimeDekho Bot is running!"
echo "=========================================="
echo ""
echo "Useful commands:"
echo "  docker compose logs -f        # View live logs"
echo "  docker compose restart bot    # Restart the bot"
echo "  docker compose down           # Stop everything"
echo "  docker compose up -d --build  # Rebuild & restart"
echo ""
echo "MongoDB data persists in Docker volume 'mongo-data'"
echo ""
