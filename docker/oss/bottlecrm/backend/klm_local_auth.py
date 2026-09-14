"""Narrow local-only session bridge added by the KLM runtime overlay."""

from __future__ import annotations

import hmac
import os

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Profile, User
from common.serializer import OrgAwareRefreshToken


class KlmLocalSessionView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        if os.environ.get("KLM_BOTTLECRM_LOCAL_MODE", "").lower() != "true":
            return Response(status=status.HTTP_404_NOT_FOUND)
        expected = os.environ.get("KLM_BOTTLECRM_BRIDGE_SECRET", "")
        provided = request.headers.get("X-KLM-Local-Secret", "")
        if len(expected) < 32 or not hmac.compare_digest(provided, expected):
            return Response(
                {"detail": "Local bridge authentication failed."},
                status=status.HTTP_403_FORBIDDEN,
            )

        email = os.environ.get("ADMIN_EMAIL", "").strip()
        try:
            user = User.objects.get(email=email, is_active=True)
            profile = Profile.objects.select_related("org").get(user=user, is_active=True)
        except (User.DoesNotExist, Profile.DoesNotExist):
            return Response(
                {"detail": "Local administrator is not initialized."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        token = OrgAwareRefreshToken.for_user_and_org(user, profile.org, profile)
        return Response(
            {
                "access_token": str(token.access_token),
                "refresh_token": str(token),
                "org_id": str(profile.org_id),
                "user_id": str(user.id),
            }
        )
