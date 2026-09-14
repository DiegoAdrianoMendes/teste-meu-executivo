#!/usr/bin/env python3
"""Create the single KLM organization/profile in the local BottleCRM runtime."""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crm.settings")
sys.path.insert(0, "/app")

import django

django.setup()

from common.models import Org, Profile, User


email = os.environ["ADMIN_EMAIL"].strip()
name = os.environ.get("KLM_OSS_BOTTLECRM_ADMIN_NAME", "Kleisle Monteiro").strip()
user = User.objects.get(email=email)
if name and user.name != name:
    user.name = name
    user.save(update_fields=["name"])

org, created = Org.objects.get_or_create(
    name="KLM Advogados",
    defaults={
        "company_name": "KLM Advogados",
        "email": email,
        "default_currency": "BRL",
        "default_country": "BR",
        "website": "https://klmadvogados.com",
    },
)
changed = []
for field, value in {
    "company_name": "KLM Advogados",
    "email": email,
    "default_currency": "BRL",
    "default_country": "BR",
    "website": "https://klmadvogados.com",
}.items():
    if getattr(org, field) != value:
        setattr(org, field, value)
        changed.append(field)
if changed:
    org.save(update_fields=changed)

profile, _ = Profile.objects.get_or_create(user=user, org=org)
profile.role = "ADMIN"
profile.is_organization_admin = True
profile.is_active = True
profile.has_sales_access = True
profile.has_marketing_access = True
profile.save(
    update_fields=[
        "role",
        "is_organization_admin",
        "is_active",
        "has_sales_access",
        "has_marketing_access",
    ]
)

print(
    json.dumps(
        {
            "created": created,
            "organizationId": str(org.id),
            "profileId": str(profile.id),
            "adminVerified": True,
        },
        sort_keys=True,
    )
)
