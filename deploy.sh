#!/bin/bash
set -euo pipefail

cd /opt/cultfit-tracker

echo "Pulling latest changes..."
git pull --ff-only origin main

echo "Building Docker image..."
docker compose build

echo "Recreating application container..."
docker compose up -d --remove-orphans

echo "Deployment complete."
docker compose ps
