from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APIAuthenticationScheme(OpenApiAuthenticationExtension):
    name = 'Token based authentication'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            "description": "Value should be formatted: `Token <key>`"
        }
