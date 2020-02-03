import requests
import json
from pprint import pformat

from .bunch import Bunch

from manila.exception import ShareBackendException
from oslo_log import log as logging
LOG = logging.getLogger(__name__)


class VastApiException(ShareBackendException):
    pass


class RESTSession(requests.Session):

    def __init__(self, *args, **kwargs):

        def init(auth, base_url, ssl_verify, **kwargs):
            super(RESTSession, self).__init__(*args, **kwargs)
            self.base_url = base_url.rstrip("/")
            self.ssl_verify = ssl_verify
            self.auth = auth
            self.headers["Accept"] = "application/json"
            self.headers["Content-Type"] = "application/json"

        init(**kwargs)

    def request(self, verb, api_method, params=None, **kwargs):
        verb = verb.upper()
        api_method = api_method.strip("/")
        url = "{self.base_url}/{api_method}/".format(**locals())
        LOG.info(">>> [{verb}] {url}".format(**locals()))

        if 'data' in kwargs:
            kwargs['data'] = json.dumps(kwargs['data'])

        if params or kwargs:
            for line in pformat(dict(kwargs, params=params)).splitlines():
                LOG.info("    %s", line)

        ret = super(RESTSession, self).request(verb, url, verify=self.ssl_verify, params=params, **kwargs)

        if ret.status_code == 503 and ret.text:
            LOG.error(ret.text)
            raise VastApiException(msg=ret.text)

        try:
            ret.raise_for_status()
        except Exception as exc:
            LOG.exception("Error requesting from %s", api_method)
            raise VastApiException(msg=str(exc))

        LOG.info("<<< [{verb}] {url}".format(**locals()))
        if ret.content:
            ret = Bunch.from_dict(ret.json())
            for line in pformat(ret).splitlines():
                LOG.info("    %s", line)
        else:
            ret = None
        LOG.info("--- [{verb}] {url}: Done".format(**locals()))
        return ret

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)

        def func(**params):
            return self.request("get", attr, params=params)

        func.__name__ = attr
        setattr(self, attr, func)
        return func
