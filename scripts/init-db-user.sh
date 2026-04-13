#!/bin/bash

# Generate random 12-character alphanumeric password
EMAIL='backend@rhobots.ai'
PASSWORD='backend'

# Run the curl request silently (no output shown)
curl -s --output /dev/null --request POST \
  --header 'Content-Type: application/json' \
  --data "{
    \"name\": \"InsurIQ\",
    \"email\": \"$EMAIL\",
    \"password\": \"$PASSWORD\"
  }" \
  http://localhost:10000/api/auth/sign-up/email

echo "email: $EMAIL"
echo "password: $PASSWORD"
