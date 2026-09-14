#!/bin/sh
set -eu

umask 077
runtime_dir=/tmp/chattermate-runtime
mkdir -p "$runtime_dir"

app_config="$({
  jq -n \
    --arg firebase_api_key "${VITE_FIREBASE_API_KEY:-}" \
    --arg firebase_auth_domain "${VITE_FIREBASE_AUTH_DOMAIN:-}" \
    --arg firebase_project_id "${VITE_FIREBASE_PROJECT_ID:-}" \
    --arg firebase_messaging_sender_id "${VITE_FIREBASE_MESSAGING_SENDER_ID:-}" \
    --arg firebase_app_id "${VITE_FIREBASE_APP_ID:-}" \
    --arg firebase_storage_bucket "${VITE_FIREBASE_STORAGE_BUCKET:-}" \
    --arg firebase_measurement_id "${VITE_FIREBASE_MEASUREMENT_ID:-}" \
    --arg firebase_vapid_key "${VITE_FIREBASE_VAPID_KEY:-}" \
    --arg google_fonts_api_key "${VITE_GOOGLE_FONTS_API_KEY:-}" \
    --arg demo_widget_id "${VITE_DEMO_WIDGET_ID:-}" \
    --arg shopify_api_key "${VITE_SHOPIFY_API_KEY:-}" \
    --arg razorpay_key_id "${VITE_RAZORPAY_KEY_ID:-}" \
    --arg gtm_id "${VITE_GTM_ID:-}" \
    '{
      FIREBASE_API_KEY: $firebase_api_key,
      FIREBASE_AUTH_DOMAIN: $firebase_auth_domain,
      FIREBASE_PROJECT_ID: $firebase_project_id,
      FIREBASE_MESSAGING_SENDER_ID: $firebase_messaging_sender_id,
      FIREBASE_APP_ID: $firebase_app_id,
      FIREBASE_STORAGE_BUCKET: $firebase_storage_bucket,
      FIREBASE_MEASUREMENT_ID: $firebase_measurement_id,
      FIREBASE_VAPID_KEY: $firebase_vapid_key,
      GOOGLE_FONTS_API_KEY: $google_fonts_api_key,
      DEMO_WIDGET_ID: $demo_widget_id,
      VITE_SHOPIFY_API_KEY: $shopify_api_key,
      RAZORPAY_KEY_ID: $razorpay_key_id,
      GTM_ID: $gtm_id,
      NODE_ENV: "production",
      HOST: "0.0.0.0"
    }'
} 2>/dev/null)"

{
  printf 'window.APP_CONFIG = %s;\n' "$app_config"
  printf 'window.APP_CONFIG.API_URL = window.location.origin + "/api/v1";\n'
  printf 'window.APP_CONFIG.WS_URL = window.location.origin;\n'
  printf 'window.APP_CONFIG.WIDGET_URL = window.location.origin;\n'
} > "$runtime_dir/config.js"

firebase_config="$({
  jq -n \
    --arg api_key "${VITE_FIREBASE_API_KEY:-}" \
    --arg auth_domain "${VITE_FIREBASE_AUTH_DOMAIN:-}" \
    --arg project_id "${VITE_FIREBASE_PROJECT_ID:-}" \
    --arg messaging_sender_id "${VITE_FIREBASE_MESSAGING_SENDER_ID:-}" \
    --arg app_id "${VITE_FIREBASE_APP_ID:-}" \
    --arg storage_bucket "${VITE_FIREBASE_STORAGE_BUCKET:-}" \
    --arg measurement_id "${VITE_FIREBASE_MEASUREMENT_ID:-}" \
    '{
      apiKey: $api_key,
      authDomain: $auth_domain,
      projectId: $project_id,
      messagingSenderId: $messaging_sender_id,
      appId: $app_id,
      storageBucket: $storage_bucket,
      measurementId: $measurement_id
    }'
} 2>/dev/null)"
printf 'const firebaseConfig = %s;\n' "$firebase_config" > "$runtime_dir/swenv.js"

exec "$@"
