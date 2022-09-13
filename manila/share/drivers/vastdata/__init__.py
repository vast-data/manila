# Copyright 2020 VAST Data Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import re
import socket
from contextlib import contextmanager
import random

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import units

from manila.common import constants
from manila import exception
from manila.i18n import _
from manila.share import driver
from manila import utils

from .rest import RESTSession, VastApiException
from .bunch import Bunch

LOG = logging.getLogger(__name__)

OPTS = [
    cfg.HostAddressOpt(
        'vast_mgmt_host',
        help='Hostname or IP address VAST storage system management VIP.'),
    cfg.StrOpt(
        'vast_vippool_name', default="manila",
        help='Name of Virtual IP pool'),
    cfg.StrOpt(
        'vast_root_export', default="manila",
        help='Name of Virtual IP pool'),
    cfg.StrOpt(
        'vast_mgmt_user',
        help='Username for VAST management'),
    cfg.StrOpt(
        'vast_mgmt_password',
        help='Password for VAST management',
        secret=True),
]


CONF = cfg.CONF
CONF.register_opts(OPTS)

_MANILA_TO_VAST_ACCESS_LEVEL = {
    constants.ACCESS_LEVEL_RW: 'nfs_read_write',
    constants.ACCESS_LEVEL_RO: 'nfs_read_only',
}


RE_IS_IP = re.compile(r"\d+\.\d+\.\d+\.\d+")


class VASTShareDriver(driver.ShareDriver):

    VERSION = '1.0'    # driver version

    def __init__(self, *args, **kwargs):
        super(VASTShareDriver, self).__init__(False, *args, **kwargs)
        self.configuration.append_config_values(OPTS)

    def do_setup(self, context):
        """Driver initialization"""

        auth = (self.configuration.vast_mgmt_user, self.configuration.vast_mgmt_password)
        self._host = self.configuration.vast_mgmt_host
        self._vippool_name = self.configuration.vast_vippool_name
        self._root_export = self.configuration.vast_root_export

        self.vms_session = RESTSession(base_url="https://{}/api".format(self._host), auth=auth, ssl_verify=False)
        try:
            metrics_spec = self.vms_session.metrics()
        except VastApiException as ex:
            msg = _("Exception when logging into the array: %s\n") % ex
            LOG.exception(msg)
            raise exception.ManilaException(message=msg)

        self._metrics_spec = {m.fqn: m for m in metrics_spec}
        backend_name = self.configuration.safe_get('share_backend_name')
        self._backend_name = backend_name or self.__class__.__name__

        manila_view = "manila"
        export = self._get_export(manila_view)
        if not export:
            policy = self._get_policy("default")
            data = dict(name=manila_view, path=self._root_export, policy_id=policy.id, create_dir=True, protocols=['NFS'])
            self.vms_session.post("views", data=data)

        LOG.debug('setup complete')

    @contextmanager
    def _mounted_root(self):
        vips = self._get_vips()
        vip = random.choice(vips).ip
        mp = "/tmp/manila/{}".format(vip)
        utils.execute("mkdir", "-p", mp)
        utils.execute("mount", "-t", "nfs", "{}:{}".format(vip, self._root_export), mp, run_as_root=True)
        try:
            yield mp
        finally:
            utils.execute("umount", mp, run_as_root=True)
            utils.execute("rmdir", mp, run_as_root=True)

    def _update_share_stats(self, data=None):
        """Retrieve stats info from share group."""
        metrics = self._get_capacity_metrics()

        data = dict(
            share_backend_name=self._backend_name,
            vendor_name='VAST STORAGE',
            driver_version=self.VERSION,
            storage_protocol='NFS',  # NFS_CIFS ?
            data_reduction=metrics.drr,
            total_capacity_gb=float(metrics.logical_space) / units.Gi,
            free_capacity_gb=float(metrics.logical_space - metrics.logical_space_in_use) / units.Gi,
            provisioned_capacity_gb=float(metrics.logical_space_in_use) / units.Gi,
            snapshot_support=True,
            create_share_from_snapshot_support=False,
            mount_snapshot_support=False,
            revert_to_snapshot_support=False)

        super(VASTShareDriver, self)._update_share_stats(data)

    def _get_capacity_metrics(self):
        metrics = [
            'Capacity,drr',
            'Capacity,logical_space',
            'Capacity,logical_space_in_use',
            'Capacity,physical_space',
            'Capacity,physical_space_in_use',
        ]

        ret = self.vms_session.get("monitors/ad_hoc_query", params=dict(
            prop_list=metrics, object_type='cluster', time_frame='1m'))
        last_sample = ret.data[-1]
        return Bunch({
            name.partition(",")[-1]: value
            for name, value in zip(ret.prop_list, last_sample)})

    def _to_volume_path(self, manila_share, root=None):
        if not root:
            root = self._root_export
        share_id = manila_share['id']
        return "{root}/manila-{share_id}".format(**locals())

    def _get_vips(self):
        vips = [
            vip for vip in self.vms_session.vips()
            if vip.vippool == self._vippool_name]
        if not vips:
            raise exception.ManilaException("VIP Pool '%s' does not exist, or has no IPs" % self._vippool_name)
        return vips

    def _get_quota(self, share_id):
        quotas = self.vms_session.quotas(name__contains=share_id)
        if not quotas:
            return
        if len(quotas) > 1:
            raise exception.ShareBackendException(message="Too many quotas found with name %s" % share_id)
        return quotas[0]

    def _get_export(self, share_id):
        exports = self.vms_session.views(name=share_id)
        if not exports:
            return
        if len(exports) > 1:
            raise exception.ShareBackendException(message="Too many exports found with name %s" % share_id)
        return exports[0]

    def _get_policy(self, share_id):
        policy = self.vms_session.viewpolicies(name=share_id)
        if not policy:
            return
        if len(policy) > 1:
            raise exception.ShareBackendException(message="Too many policy found with name %s" % share_id)
        return policy[0]

    def ensure_share(self, context, share, share_server=None):
        share_proto = share['share_proto']
        if share_proto != 'NFS':
            raise exception.InvalidShare(reason=_('Invalid NAS protocol supplied: %s.' % share_proto))

        vips = self._get_vips()

        share_id = share['id']
        requested_capacity = share['size'] * units.Gi
        path = self._to_volume_path(share)

        policy = self._get_policy(share_id)
        if not policy:
            data = dict(name=share_id)
            policy = self.vms_session.post("viewpolicies", data=data)

        quota = self._get_quota(share_id)
        if not quota:
            data = dict(name=share_id, path=path, create_dir=True, hard_limit=requested_capacity)
            quota = self.vms_session.post("quotas", data=data)
            LOG.debug("Quota created: %s -> %s", quota.id, path)
        elif quota.hard_limit != requested_capacity:
            raise exception.ManilaException(
                "Share already exists with different capacity "
                "(requested={requested_capacity}, exists={quota.hard_limit})".format(**locals()))

        export = self._get_export(share_id)
        if not export:
            data = dict(name=share_id, path=path, create_dir=True, policy_id=policy.id, protocols=['NFS'])
            self.vms_session.post("views", data=data)
        elif not export.policy == share_id:
            self.vms_session.patch("views/{}".format(export.id), data=dict(policy_id=policy.id))

        return [dict(
            path='{vip.ip}:{path}'.format(vip=vip, path=path),
            metadata=dict(quota_id=quota.id),
            is_admin_only=False,
        ) for vip in vips]

    def create_share(self, context, share, share_server=None):
        return self.ensure_share(context, share, share_server)[0]

    def delete_share(self, context, share, share_server=None):
        """Called to delete a share"""

        share_id = share['id']

        export = self._get_export(share_id)
        if export:
            self.vms_session.delete("views/{export.id}".format(**locals()))
        else:
            LOG.warning("export %s not found on VAST, skipping delete", share_id)

        quota = self._get_quota(share_id)
        if quota:
            self.vms_session.delete("quotas/{quota.id}".format(**locals())),
        else:
            LOG.warning("quota %s not found on VAST, skipping delete", share_id)

        policy = self._get_policy(share_id)
        if policy:
            self.vms_session.delete("viewpolicies/{policy.id}".format(**locals())),
        else:
            LOG.warning("policy %s not found on VAST, skipping delete", share_id)

        with self._mounted_root() as mount:
            src = self._to_volume_path(share, mount)
            dst = "{}/deleted/{}".format(mount, share_id)
            utils.execute("mkdir", "-p", "{}/deleted".format(mount), run_as_root=True)
            utils.execute("mv", src, dst, run_as_root=True)

    def update_access(self, context, share, access_rules, add_rules,
                      delete_rules, share_server=None):

        if share['share_proto'] != 'NFS':
            return

        validate_access_rules(access_rules)

        share_id = share['id']

        export = self._to_volume_path(share)
        LOG.info("Changing access on %s", share_server)
        levels = {rule['access_level'] for rule in access_rules if rule}
        if not levels:
            return

        access_types = {_MANILA_TO_VAST_ACCESS_LEVEL[l] for l in levels}

        def reverse_lookup(dns):
            if RE_IS_IP.match(dns):
                return [dns]
            try:
                hostname, aliaslist, ipaddrlist = socket.gethostbyname_ex(dns)
            except socket.gaierror as exc:
                LOG.error("Failed to resolve host '%s': %s (ignoring)", dns, exc)
                return []

            LOG.info("resolved %s: %s", hostname, ", ".join(ipaddrlist))
            return ipaddrlist

        allowed_hosts = [ip for rule in access_rules if rule for ip in reverse_lookup(rule['access_to'])]

        LOG.info("Changing access on %s -> %s (%s)", export, allowed_hosts, access_types)

        access_type_mapping = dict.fromkeys(access_types, allowed_hosts)
        data = {"name": share_id, "nfs_no_squash": ["*"], "nfs_root_squash": ["*"]}
        data.update(access_type_mapping)
        policy = self._get_policy(share_id)
        if policy:
            self.vms_session.patch("viewpolicies/{}".format(policy.id), data=data)
        else:
            self.vms_session.post("viewpolicies", data=data)

    def _resize_share(self, share, new_size):
        share_id = share['id']
        quota = self._get_quota(share_id)
        if not quota:
            raise exception.ShareNotFound(reason="Share not found", share_id=share_id)

        requested_capacity = new_size * units.Gi
        self.vms_session.patch("quotas/{}".format(quota.id), data=dict(hard_limit=requested_capacity))

    def extend_share(self, share, new_size, share_server=None):
        """uses resize_share to extend a share"""
        self._resize_share(share, new_size)

    def shrink_share(self, share, new_size, share_server=None):
        """uses resize_share to shrink a share"""
        self._resize_share(share, new_size)

    def create_snapshot(self, context, snapshot, share_server):
        """Is called to create snapshot."""
        path = self._to_volume_path(dict(id=snapshot['share_instance_id']))
        LOG.info("Creating snapshot for %s", path)
        snapshot = self.vms_session.post("snapshots", data=dict(
            path=path,
            name=snapshot['name'],
        ))

    def delete_snapshot(self, context, snapshot, share_server):
        """Is called to remove share."""
        name = snapshot['name']
        snapshots = self.vms_session.snapshots(name=name)
        assert len(snapshots) == 1, "Too many snapshots with name {!r}".format(name)
        self.vms_session.delete("snapshots/{}".format(snapshots[0].id))

    def get_network_allocations_number(self):
        return 0


def validate_access_rules(access_rules):

    allowed_types = {'ip'}
    allowed_levels = _MANILA_TO_VAST_ACCESS_LEVEL.keys()

    for access in (access_rules or []):
        access_type = access['access_type']
        access_level = access['access_level']
        if access_type not in allowed_types:
            reason = _("Only %s access type allowed.") % (
                ', '.join(tuple(["'%s'" % x for x in allowed_types])))
            raise exception.InvalidShareAccess(reason=reason)
        if access_level not in allowed_levels:
            raise exception.InvalidShareAccessLevel(level=access_level)
