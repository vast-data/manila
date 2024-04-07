import requests
import json
from pprint import pformat
from abc import ABC
from requests.utils import default_user_agent
from cachetools import cached, TTLCache
from oslo_utils.versionutils import is_compatible
from tenacity import retry, retry_if_exception_type, stop_after_attempt, TryAgain
from manila.share.drivers.vastdata.driver_util import Bunch, generate_ip_range
from manila.exception import ShareBackendException
from oslo_log import log as logging

LOG = logging.getLogger(__name__)


# Custom exception for VAST API related errors
class VastApiException(ShareBackendException):
    pass


class Session(requests.Session):

    def __init__(self, host, username, password, ssl_verify, plugin_version):
        super().__init__()
        self.base_url = f"https://{host.strip('/')}/api"
        self.ssl_verify = ssl_verify
        self.username = username
        self.password = password
        self.headers["Accept"] = "application/json"
        self.headers["Content-Type"] = "application/json"
        self.headers["User-Agent"] = f"manila/v{plugin_version} ({default_user_agent()})"

        if not ssl_verify:
            import urllib3
            urllib3.disable_warnings()

    def refresh_auth_token(self):
        try:
            resp = super().request(
                "POST", f"{self.base_url}/token/", verify=self.ssl_verify, timeout=5,
                json={"username": self.username, "password": self.password}
            )
            resp.raise_for_status()
            token = resp.json()["access"]
            self.headers['authorization'] = f"Bearer {token}"
        except ConnectionError as e:
            raise VastApiException(
                message=f"The vms on the designated host {self.base_url} "
                     f"cannot be accessed. Please verify the specified endpoint. "
                     f"origin error: {e}"
                )

    @retry(retry=retry_if_exception_type(TryAgain), stop=stop_after_attempt(3), reraise=True)
    def request(self, verb, api_method, params=None,  log_result=True, **kwargs):
        verb = verb.upper()
        api_method = api_method.strip("/")
        url = f"{self.base_url}/{api_method}/"
        LOG.info(f">>> [{verb}] {url}")

        if 'data' in kwargs:
            kwargs['data'] = json.dumps(kwargs['data'])

        if params or kwargs:
            if log_result:
                for line in pformat(dict(kwargs, params=params)).splitlines():
                    LOG.info("    %s", line)
            else:
                LOG.info("*** request payload is hidden ***")

        ret = super().request(verb, url, verify=self.ssl_verify, params=params, **kwargs)
        if ret.status_code == 403 and "Token is invalid" in ret.text:
            self.refresh_auth_token()
            raise TryAgain("refresh token")

        if ret.status_code in (400, 503) and ret.text:
            raise VastApiException(msg=ret.text)

        try:
            ret.raise_for_status()
        except Exception as exc:
            raise VastApiException(msg=str(exc))

        LOG.info(f"<<< [{verb}] {url}")
        if ret.content:
            ret = Bunch.from_dict(ret.json())
            for line in pformat(ret).splitlines():
                LOG.info("    %s", line)
        else:
            ret = None
        LOG.info(f"--- [{verb}] {url}: Done")
        return ret

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)

        def func(**params):
            return self.request("get", attr, params=params)

        func.__name__ = attr
        setattr(self, attr, func)
        return func


def requisite(semver: str, operation: str = None, ignore: bool = False):
    """
    Use this decorator to indicate the minimum required version of the VAST cluster
     for invoking the API that is being decorated.
    Decorator works in two modes:
    1. When ignore == False and version mismatch detected then `OperationNotSupported` exception will be thrown
    2. When ignore == True and version mismatch detected then method decorated method execution never happened
    """

    def dec(fn):

        def _args_wrapper(self, *args, **kwargs):

            sw_version = self.rest.get_sw_version()
            if not is_compatible(semver, sw_version, same_major=False):
                if ignore:
                    return
                op = operation or fn.__name__
                raise ShareBackendException(
                    f"Operation {op} is not supported on VAST version {sw_version}."
                    f" Required version is {semver}"
                )
            return fn(self, *args, **kwargs)

        return _args_wrapper

    return dec


class VastResource(ABC):
    resource_name = None

    def __init__(self, rest):
        self.rest = rest  # For intercommunication between resources.
        self.session = rest.session

    def list(self, **params):
        """Get list of entries with optional filtering params"""
        return self.session.get(self.resource_name, params=params)

    def create(self, **params):
        """Create new entry with provided params"""
        return self.session.post(self.resource_name, data=params)

    def update(self, entry_id, **params):
        """Update entry by id with provided params"""
        return self.session.patch(f"{self.resource_name}/{entry_id}", data=params)

    def delete(self, name):
        """Delete entry by name. Skip if entry not found."""
        entry = self.one(name)
        if not entry:
            resource = self.__class__.__name__.lower()
            LOG.warning(f"{resource} {name} not found on VAST, skipping delete")
            return
        return self.session.delete(f"{self.resource_name}/{entry.id}")

    def one(self, name):
        """Get single entry by name. Raise exception if multiple entries found."""
        entries = self.list(name=name)
        if not entries:
            return
        if len(entries) > 1:
            resource = self.__class__.__name__.lower() + "s"
            raise ShareBackendException(message=f"Too many {resource} found with name {name}")
        return entries[0]

    def ensure(self, name, **params):
        entry = self.one(name)
        if not entry:
            entry = self.create(name=name, **params)
        return entry


class View(VastResource):
    resource_name = "views"

    def create(self, name, path, policy_id):
        data = dict(name=name, path=path, policy_id=policy_id, create_dir=True, protocols=['NFS'])
        return super().create(**data)


class ViewPolicy(VastResource):
    resource_name = "viewpolicies"


class CapacityMetrics(VastResource):

    def get(self, metrics, object_type="cluster", time_frame="1m"):
        """Get capacity metrics for the cluster"""
        params = dict(prop_list=metrics, object_type=object_type, time_frame=time_frame)
        ret = self.session.get("monitors/ad_hoc_query", params=params)
        last_sample = ret.data[-1]
        return Bunch({
            name.partition(",")[-1]: value
            for name, value in zip(ret.prop_list, last_sample)}
        )


class Quota(VastResource):
    resource_name = "quotas"


class VipPool(VastResource):
    resource_name = "vippools"

    def vips(self, pool_name):
        """Get list of ip addresses from vip pool"""
        vippool = self.one(name=pool_name)
        if not vippool:
            raise ShareBackendException(f"No vip pool found with name {pool_name}")
        vips = generate_ip_range(vippool.ip_ranges)
        if not vips:
            raise ShareBackendException(f"Pool {pool_name} has no available vips")
        return vips


class Snapshots(VastResource):
    resource_name = "snapshots"


class Folders(VastResource):
    resource_name = "folders"

    @requisite(semver="4.7.0")
    def delete(self, path):
        try:
            self.session.delete(f"{self.resource_name}/delete_folder/", data=dict(path=path))
        except VastApiException as e:
            exc_msg = str(e)
            if "no such directory" in exc_msg:
                LOG.debug(f"Remote directory might have been removed earlier. ({e})")
            elif "trash folder disabled" in exc_msg:
                raise ShareBackendException("Trash Folder Access is disabled (see Settings/Cluster/Features in VMS)")
            else:
                # unpredictable error
                raise


class RestApi:

    def __init__(self, host, username, password, ssl_verify, plugin_version):
        self.session = Session(
            host=host, username=username, password=password, ssl_verify=ssl_verify, plugin_version=plugin_version
        )
        self.views = View(self)
        self.view_policies = ViewPolicy(self)
        self.capacity_metrics = CapacityMetrics(self)
        self.quotas = Quota(self)
        self.vip_pools = VipPool(self)
        self.snapshots = Snapshots(self)
        self.folders = Folders(self)

        # Refresh auth token to avoid initial "forbidden" status error.
        self.session.refresh_auth_token()

    @cached(cache=TTLCache(ttl=60 * 60, maxsize=1))
    def get_sw_version(self):
        """Software version of cluster Rest API interacts with"""
        return self.session.versions(status="success")[0].sys_version
