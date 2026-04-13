import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jwt.utils import base64url_decode
from urllib.parse import urlparse

from config import settings

# Better Auth configuration
RHOBOTS_AUTH_EP = settings.BETTER_AUTH_URL
_parsed_url = urlparse(RHOBOTS_AUTH_EP)
RHOBOTS_AUTH_ISSUER = f"{_parsed_url.scheme}://{_parsed_url.netloc}"
RHOBOTS_AUTH_JWKS_URL = f"{RHOBOTS_AUTH_EP if settings.APP_ENV.upper() == 'PROD' else 'http://auth:10000'}/api/auth/jwks"
RHOBOTS_AUTH_AUDIENCE = f"{_parsed_url.scheme}://{_parsed_url.netloc}"

def get_jwks():
    response = requests.get(RHOBOTS_AUTH_JWKS_URL)
    response.raise_for_status()
    return response.json()

def get_signing_key(jwks, token):
    unverified_header = jwt.get_unverified_header(token)
    for key in jwks["keys"]:
        if key["kid"] == unverified_header["kid"]:
            if key["kty"] == "OKP" and key["crv"] == "Ed25519":  # EdDSA key
                x = base64url_decode(key["x"])
                return Ed25519PublicKey.from_public_bytes(x)
            else:
                raise InvalidTokenError(f"Unsupported key type: {key['kty']}/{key.get('crv')}")
    raise InvalidTokenError("No matching key found in JWKS")


def verify_better_auth_token(token):
    try:
        # 1. Get JWKS
        jwks = get_jwks()

        # 2. Get the appropriate signing key
        signing_key = get_signing_key(jwks, token)

        # 3. Verify the token
        payload = jwt.decode(
            token,
            key=signing_key,
            algorithms=["EdDSA"],  # Better Auth uses RS256
            audience=RHOBOTS_AUTH_AUDIENCE,
            issuer=RHOBOTS_AUTH_ISSUER,
            options={
                "require": ["exp", "iat", "aud", "iss"],
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            }
        )

        return payload

    except ExpiredSignatureError:
        return {"error": "Token expired"}
    except InvalidTokenError as e:
        return {"error": f"Invalid token: {str(e)}"}
    except Exception as e:
        return {"error": f"Verification failed: {str(e)}"}