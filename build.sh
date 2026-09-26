#!/bin/bash
set -e

# Coloured output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Defaults
VERSION=${1:-"latest"}
IMAGE_NAME="${IMAGE_NAME:-panwatch}"

echo -e "${GREEN}🚀 PanWatch build script${NC}"
echo -e "Version: ${YELLOW}${VERSION}${NC}"
echo ""

# Check dependencies
command -v node >/dev/null 2>&1 || { echo -e "${RED}Node.js is required${NC}"; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo -e "${RED}pnpm is required${NC}"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo -e "${RED}Docker is required${NC}"; exit 1; }

# Step 1: build the frontend
echo -e "${GREEN}📦 Building the frontend...${NC}"
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..

# Step 2: copy the frontend build into the static directory
echo -e "${GREEN}📁 Copying static files...${NC}"
rm -rf static
mkdir -p static
cp -r frontend/dist/* static/

# Step 3: build the Docker image (amd64, for most servers/NAS)
echo -e "${GREEN}🐳 Building the Docker image (linux/amd64)...${NC}"
FULL_IMAGE="${IMAGE_NAME}:${VERSION}"

docker build --platform linux/amd64 --build-arg VERSION="${VERSION}" -t "${FULL_IMAGE}" .

# If the version isn't latest, tag latest too
if [ "$VERSION" != "latest" ]; then
    docker tag "${FULL_IMAGE}" "${IMAGE_NAME}:latest"
    echo -e "${GREEN}✅ Image built: ${YELLOW}${FULL_IMAGE}${NC} and ${YELLOW}${IMAGE_NAME}:latest${NC}"
else
    echo -e "${GREEN}✅ Image built: ${YELLOW}${FULL_IMAGE}${NC}"
fi

# Clean up
rm -rf static

echo ""
echo -e "${GREEN}🎉 Build complete!${NC}"
echo ""
echo "Run the container:"
echo -e "  ${YELLOW}docker run -d -p 8000:8000 -v panwatch_data:/app/data ${FULL_IMAGE}${NC}"
echo ""
echo "Push the image:"
echo -e "  ${YELLOW}docker push ${FULL_IMAGE}${NC}"
if [ "$VERSION" != "latest" ]; then
    echo -e "  ${YELLOW}docker push ${IMAGE_NAME}:latest${NC}"
fi
