"""Explicit proxy policy and a shared refusal to follow API redirects."""
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code,
                                     "API redirects are not followed", headers, fp)


def build_opener(config):
    handlers = [NoRedirect()]
    if not config.get("use_proxy", True):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)
