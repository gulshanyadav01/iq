import traceback
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.authentication import get_authorization_header

from config.authentication.auth_backend import verify_better_auth_token


class JWTAuthenticationMiddleware(BaseAuthentication):
    def authenticate(self, request, **kwargs):
        try:
            http_origin = (
                request.META["HTTP_ORIGIN"] if "HTTP_ORIGIN" in request.META else None
            )

            request.is_platform = False
            request.organization = None

            if http_origin:
                parsed_url = urlparse(http_origin)

                host_parts = parsed_url.hostname.split(".")
                subdomain = None
                if len(host_parts) >= 2:
                    subdomain = host_parts[0]
                if subdomain is not None and subdomain not in ["documentlm"]:
                    request.organization = None

            auth = get_authorization_header(request).split()

            if not auth:
                return None
            if len(auth) == 1:
                msg = _("Invalid token header. No credentials provided.")
                raise exceptions.AuthenticationFailed(msg)
            elif len(auth) > 2:
                msg = _(
                    "Invalid token header. " "Token string should not contain spaces."
                )
                raise exceptions.AuthenticationFailed(msg)

            payload = verify_better_auth_token(auth[1])

            if "error" in payload:
                raise Exception(payload["error"])

            user = (
                get_user_model()
                .objects.filter(identity_provider_id=payload["sub"])
                .first()
            )
            if user is None:
                name = payload.get("name") or ""
                name_parts = name.split(" ", 1)
                user = get_user_model().objects.create(
                    email=payload["email"],
                    identity_provider_id=payload["sub"],
                    first_name=name_parts[0] if name_parts[0] else None,
                    last_name=name_parts[1] if len(name_parts) > 1 else None,
                    profile_image_url=payload.get("image"),
                    is_email_verified=payload.get("emailVerified", False),
                    is_signed_up=True,
                )
            return user, None

        except Exception:
            print(traceback.format_exc())
            request.error_message = "Unable to authenticate user"
            return None
