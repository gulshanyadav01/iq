from rest_framework import renderers


class SSERenderer(renderers.BaseRenderer):
    media_type = 'text/event-stream'
    format = 'sse'
    charset = None
    render_style = 'binary'

    def render(self, data, media_type=None, renderer_context=None):
        return data
