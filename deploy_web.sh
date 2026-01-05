#!/bin/bash
set -e

echo "Building the project..."
npm --prefix web run build

echo "Deploying to Firebase Hosting..."
firebase deploy --only hosting --project schoopet-web

echo "Deployment complete! Visit https://www.schoopet.com"
