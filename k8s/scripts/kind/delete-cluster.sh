#!/bin/bash
set -e

echo "Deleting kind cluster 'insure-iq'..."

kind delete cluster --name insure-iq

echo "Cluster deleted successfully!"