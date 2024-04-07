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

"""
VAST's Share Driver


Configuration:


[DEFAULT]
enabled_share_backends = vast_backend_11

[vast_backend_11]
share_driver = manila.share.drivers.vastdata.VASTShareDriver
share_backend_name = vast11
snapshot_support = true
driver_handles_share_servers = false
vast_mgmt_host = v11
vast_vippool_name = vippool-1
vast_root_export = manila
vast_mgmt_user = admin
vast_mgmt_password = 123456
"""

import socket
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import units

from manila.common import constants
from manila import exception
from manila.i18n import _
from manila.share import driver
from manila import utils

from manila.share.drivers.vastdata.rest import RestApi
from manila.share.drivers.vastdata.driver_util import Bunch

LOG = logging.getLogger(__name__)

OPTS = [
    cfg.HostAddressOpt(
        'vast_mgmt_host',
        help='Hostname or IP address VAST storage system management VIP.'),
    cfg.StrOpt(
        'vast_vippool_name',
        help='Name of Virtual IP pool'),
    cfg.StrOpt(
        'vast_root_export', default="manila",
        help='Base path for shares'),
    cfg.StrOpt(
        'vast_mgmt_user',
        help='Username for VAST management'),
    cfg.StrOpt(
        'vast_mgmt_password',
        help='Password for VAST management',
        secret=True)
]

CONF = cfg.CONF
CONF.register_opts(OPTS)

MANILA_TO_VAST_ACCESS_LEVEL = {
    constants.ACCESS_LEVEL_RW: 'nfs_read_write',
    constants.ACCESS_LEVEL_RO: 'nfs_read_only',
}


class VASTShareDriver(driver.ShareDriver):
    VERSION = '1.0'  # driver version

    def __init__(self, *args, **kwargs):
        super().__init__(False, *args, **kwargs)
        self.configuration.append_config_values(OPTS)

    def do_setup(self, context):
        """Driver initialization"""
        backend_name = self.configuration.safe_get('share_backend_name')
        self._backend_name = backend_name or self.__class__.__name__
        self._vippool_name = self.configuration.vast_vippool_name
        self._root_export = "/" + self.configuration.vast_root_export.strip("/")

        username = self.configuration.vast_mgmt_user
        password = self.configuration.vast_mgmt_password
        host = self.configuration.vast_mgmt_host
        self.rest = RestApi(host, username, password, False, self.VERSION)
        LOG.debug('setup complete')

    def _update_share_stats(self, data=None):
        """Retrieve stats info from share group."""
        metrics_list = [
            'Capacity,drr',
            'Capacity,logical_space',
            'Capacity,logical_space_in_use',
            'Capacity,physical_space',
            'Capacity,physical_space_in_use',
        ]
        metrics = self.rest.capacity_metrics.get(metrics_list)
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

        super()._update_share_stats(data)

    def _to_volume_path(self, share_id, root=None):
        if not root:
            root = self._root_export
        return f"{root}/manila-{share_id}"

    def ensure_share(self, context, share, share_server=None):
        share_proto = share['share_proto']
        if share_proto != 'NFS':
            raise exception.InvalidShare(reason=_('Invalid NAS protocol supplied: {}.'.format(share_proto)))

        vips = self.rest.vip_pools.vips(pool_name=self._vippool_name)

        share_id = share['id']
        requested_capacity = share['size'] * units.Gi
        path = self._to_volume_path(share_id)
        policy = self.rest.view_policies.ensure(name=share_id)
        quota = self.rest.quotas.ensure(name=share_id, path=path, create_dir=True, hard_limit=requested_capacity)
        if quota.hard_limit != requested_capacity:
            raise exception.ManilaException(
                f"Share already exists with different capacity (requested={requested_capacity}, exists={quota.hard_limit})")

        view = self.rest.views.ensure(name=share_id, path=path, policy_id=policy.id)
        if not view.policy == share_id:
            self.rest.views.update(view.id, policy_id=policy.id)
        return [dict(path=f"{vip}:{path}", is_admin_only=False) for vip in vips]

    def create_share(self, context, share, share_server=None):
        return self.ensure_share(context, share, share_server)[0]

    def delete_share(self, context, share, share_server=None):
        """Called to delete a share"""
        share_id = share['id']
        src = self._to_volume_path(share_id)
        LOG.info(f"deleting '{src}'")
        self.rest.folders.delete(path=src)
        self.rest.views.delete(name=share_id)
        self.rest.quotas.delete(name=share_id)
        self.rest.view_policies.delete(name=share_id)

    def update_access(self, context, share, access_rules, add_rules, delete_rules, share_server=None):
        if not (add_rules or delete_rules):
            add_rules = access_rules

        if share['share_proto'] != 'NFS':
            return

        validate_access_rules(add_rules)

        share_id = share['id']
        export = self._to_volume_path(share_id)

        LOG.info(f"changing access on {share_server}")
        data = {"name": share_id, "nfs_no_squash": ["*"], "nfs_root_squash": ["*"]}

        policy = self.rest.view_policies.one(name=share_id)
        if add_rules:
            policy_rules = policy_payload_from_rules(rules=add_rules, policy=policy, action="update")
            data.update(policy_rules)
            LOG.info(f"changing access on {export}. Rules: {policy_rules}")
            if policy:
                self.rest.view_policies.update(policy.id, **data)
            else:
                self.rest.view_policies.create(**data)

        elif delete_rules:
            policy_rules = policy_payload_from_rules(rules=delete_rules, policy=policy, action="deny")
            LOG.info(f"changing access on {export}. Rules: {policy_rules}")
            data.update(policy_rules)
            self.rest.view_policies.update(policy.id, **data)

    def extend_share(self, share, new_size, share_server=None):
        """uses resize_share to extend a share"""
        self._resize_share(share, new_size)

    def shrink_share(self, share, new_size, share_server=None):
        """uses resize_share to shrink a share"""
        self._resize_share(share, new_size)

    def create_snapshot(self, context, snapshot, share_server):
        """Is called to create snapshot."""
        path = self._to_volume_path(snapshot['share_instance_id'])
        self.rest.snapshots.create(path=path, name=snapshot['name'])

    def delete_snapshot(self, context, snapshot, share_server):
        """Is called to remove share."""
        self.rest.snapshots.delete(name=snapshot["name"])

    def get_network_allocations_number(self):
        return 0

    def _resize_share(self, share, new_size):
        share_id = share['id']
        quota = self.rest.quotas.one(name=share_id)
        if not quota:
            raise exception.ShareNotFound(reason="Share not found", share_id=share_id)

        requested_capacity = new_size * units.Gi
        self.rest.quotas.update(quota.id, hard_limit=requested_capacity)


def policy_payload_from_rules(rules, policy, action):
    """Convert list of manila rules into vast compatible payload for updating/creating policy."""

    def reverse_lookup(dns):
        if utils.is_valid_ip_address(dns, [4]):
            return [dns]
        try:
            hostname, aliaslist, ipaddrlist = socket.gethostbyname_ex(dns)
        except socket.gaierror as exc:
            LOG.error(f"failed to resolve host '{dns}': {exc} (ignoring)")
            return []

        LOG.info(f"resolved {hostname}: {', '.join(ipaddrlist)}")
        return ipaddrlist

    hosts = {
        MANILA_TO_VAST_ACCESS_LEVEL[rule['access_level']]:
            {ip for ip in reverse_lookup(rule['access_to'])}
        for rule in rules or []
    }

    _default_rules = set()

    # Delete default_vast_policy on each update. There is no sense to keep * in list of allowed/denied hosts
    # as user want to set particular ip/ips only.
    _default_vast_policy = {"*"}
    if action == "update":
        rw = set(policy.nfs_read_write) | hosts.get('nfs_read_write', _default_rules)
        ro = set(policy.nfs_read_only) | hosts.get('nfs_read_only', _default_rules)
    elif action == "deny":
        rw = set(policy.nfs_read_write) - hosts.get('nfs_read_write', _default_rules)
        ro = set(policy.nfs_read_only) - hosts.get('nfs_read_only', _default_rules)
    else:
        raise ValueError("Invalid action")

    # When policy created default access is "*" for read-write and read-only operations.
    # After updating any of rules (rw or ro) we need to delete "*" to prevent ambiguous state when
    # resource available for certain ip and for all range of ip addresses.
    if len(rw) > 1:
        rw -= _default_vast_policy

    if len(ro) > 1:
        rw -= _default_vast_policy

    return {
        'nfs_read_write': list(rw),
        'nfs_read_only': list(ro)
        }


def validate_access_rules(access_rules):
    allowed_types = {'ip'}
    allowed_levels = MANILA_TO_VAST_ACCESS_LEVEL.keys()

    for access in (access_rules or []):
        access_type = access['access_type']
        access_level = access['access_level']
        if access_type not in allowed_types:
            reason = _("Only {} access type allowed.").format(
                ', '.join(tuple([f"'{x}'" for x in allowed_types])))
            raise exception.InvalidShareAccess(reason=reason)
        if access_level not in allowed_levels:
            raise exception.InvalidShareAccessLevel(level=access_level)
