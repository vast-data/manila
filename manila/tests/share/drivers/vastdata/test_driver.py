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
from itertools import product
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from ddt import data
from ddt import ddt
from ddt import idata
from ddt import unpack
from manila import context
import manila.exception as exception
from manila.share import configuration
from manila.share.drivers.vastdata.driver_util import Bunch
from manila.share.drivers.vastdata import policy_payload_from_rules
from manila.share.drivers.vastdata import validate_access_rules
from manila.share.drivers.vastdata import VASTShareDriver
from manila.tests import fake_share
from manila.tests.share.drivers.vastdata.test_rest import fake_metrics
import socket


@patch(
    "manila.share.drivers.vastdata.rest.Session.refresh_auth_token",
    MagicMock()
)
@ddt
class VASTShareDriverTestCase(unittest.TestCase):
    def _create_mocked_rest_api(self):
        # Create a mock RestApi instance
        mock_rest_api = MagicMock()

        # Create mock sub resources with their methods
        subresources = [
            "views",
            "view_policies",
            "capacity_metrics",
            "quotas",
            "vip_pools",
            "snapshots",
            "folders",
        ]
        methods = [
            "list", "create", "update",
            "delete", "one", "ensure", "vips"
        ]

        for subresource in subresources:
            mock_subresource = MagicMock()
            setattr(mock_rest_api, subresource, mock_subresource)

            for method in methods:
                mock_method = MagicMock()
                setattr(mock_subresource, method, mock_method)

        return mock_rest_api

    @patch("manila.share.drivers.vastdata.rest.Session.refresh_auth_token")
    def setUp(self, m_auth_token):
        super().setUp()
        self.fake_conf = configuration.Configuration(None)
        self._context = context.get_admin_context()
        self._snapshot = fake_share.fake_snapshot_instance()

        self.fake_conf.set_default("driver_handles_share_servers", False)
        self.fake_conf.set_default("share_backend_name", "vast")
        self.fake_conf.set_default("vast_mgmt_host", "test")
        self.fake_conf.set_default("vast_root_export", "/fake")
        self.fake_conf.set_default("vast_vippool_name", "vippool")
        self.fake_conf.set_default("vast_mgmt_user", "user")
        self.fake_conf.set_default("vast_mgmt_password", "password")
        self._driver = VASTShareDriver(
            execute=MagicMock(), configuration=self.fake_conf
        )
        self._driver.do_setup(self._context)
        m_auth_token.assert_called_once()

    def test_do_setup(self):
        session = self._driver.rest.session
        self.assertEqual(self._driver._backend_name, "vast")
        self.assertEqual(self._driver._vippool_name, "vippool")
        self.assertEqual(self._driver._root_export, "/fake")
        self.assertFalse(session.ssl_verify)
        self.assertEqual(session.base_url, "https://test/api")

    @patch(
        "manila.share.drivers.vastdata.rest.Session.get",
        MagicMock(return_value=fake_metrics),
    )
    def test_update_share_stats(self):
        self._driver._update_share_stats()
        result = self._driver._stats
        self.assertEqual(result["share_backend_name"], "vast")
        self.assertEqual(result["driver_handles_share_servers"], False)
        self.assertEqual(result["vendor_name"], "VAST STORAGE")
        self.assertEqual(result["driver_version"], "1.0")
        self.assertEqual(result["storage_protocol"], "NFS")
        self.assertEqual(result["total_capacity_gb"], 471.1061706542969)
        self.assertEqual(result["free_capacity_gb"], 450.2256333641708)
        self.assertEqual(result["reserved_percentage"], 0)
        self.assertEqual(result["reserved_snapshot_percentage"], 0)
        self.assertEqual(result["reserved_share_extend_percentage"], 0)
        self.assertIs(result["qos"], False)
        self.assertIsNone(result["pools"])
        self.assertIs(result["snapshot_support"], True)
        self.assertIs(result["create_share_from_snapshot_support"], False)
        self.assertIs(result["revert_to_snapshot_support"], False)
        self.assertIs(result["mount_snapshot_support"], False)
        self.assertIsNone(result["replication_domain"])
        self.assertIsNone(result["filter_function"])
        self.assertIsNone(result["goodness_function"])
        self.assertIs(result["security_service_update_support"], False)
        self.assertIs(result["network_allocation_update_support"], False)
        self.assertIs(result["share_server_multiple_subnet_support"], False)
        self.assertIs(result["mount_point_name_support"], False)
        self.assertEqual(result["data_reduction"], 1.2)
        self.assertEqual(result["provisioned_capacity_gb"], 20.880537290126085)
        self.assertEqual(
            result["share_group_stats"],
            {"consistent_snapshot_support": None}
        )
        self.assertIs(result["ipv4_support"], True)
        self.assertIs(result["ipv6_support"], False)

    @idata(product([1073741824, 1], ["NFS", "SMB"], ["fakeid", None]))
    @unpack
    def test_ensure_share(self, capacity, proto, policy):
        share = fake_share.fake_share(share_proto=proto)
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.ensure.return_value = Bunch(id=1)
        mock_rest.quotas.ensure.return_value = Bunch(id=2, hard_limit=capacity)
        mock_rest.views.ensure.return_value = Bunch(id=3, policy=policy)
        mock_rest.vip_pools.vips.return_value = ["1.1.1.0", "1.1.1.1"]
        with patch.object(self._driver, "rest", mock_rest):
            if proto != "NFS":
                with self.assertRaises(exception.InvalidShare) as exc:
                    self._driver.ensure_share(self._context, share)
                self.assertIn(
                    "Invalid NAS protocol supplied",
                    str(exc.exception)
                )
            elif capacity == 1:
                with self.assertRaises(exception.ManilaException) as exc:
                    self._driver.ensure_share(self._context, share)
                self.assertIn(
                    "Share already exists with different capacity",
                    str(exc.exception)
                )
            else:
                locations = self._driver.ensure_share(self._context, share)
                mock_rest.vip_pools.vips.assert_called_once_with(
                    pool_name="vippool"
                )
                mock_rest.view_policies.ensure.assert_called_once_with(
                    name="fakeid"
                )
                mock_rest.quotas.ensure.assert_called_once_with(
                    name="fakeid",
                    path="/fake/manila-fakeid",
                    create_dir=True,
                    hard_limit=capacity,
                )
                mock_rest.views.ensure.assert_called_once_with(
                    name="fakeid", path="/fake/manila-fakeid", policy_id=1
                )
                self.assertListEqual(
                    locations,
                    [
                        {
                            "path": "1.1.1.0:/fake/manila-fakeid",
                            "is_admin_only": False
                        },
                        {
                            "path": "1.1.1.1:/fake/manila-fakeid",
                            "is_admin_only": False
                        },
                    ],
                )
                if not policy:
                    mock_rest.views.update.assert_called_once_with(
                        3, policy_id=1
                    )
                else:
                    mock_rest.views.update.assert_not_called()

    def test_create_share(self):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.ensure.return_value = Bunch(id=1)
        mock_rest.quotas.ensure.return_value = Bunch(
            id=2, hard_limit=1073741824
        )
        mock_rest.views.ensure.return_value = Bunch(
            id=3, policy=None
        )
        mock_rest.vip_pools.vips.return_value = ["1.1.1.0", "1.1.1.1"]
        with patch.object(self._driver, "rest", mock_rest):
            result = self._driver.create_share(self._context, share)
        self.assertDictEqual(
            result, {
                "path": "1.1.1.0:/fake/manila-fakeid", "is_admin_only": False
            }
        )

    def test_delete_share(self):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.delete_share(self._context, share)
        mock_rest.folders.delete.assert_called_once_with(
            path="/fake/manila-fakeid"
        )
        mock_rest.views.delete.assert_called_once_with(name="fakeid")
        mock_rest.quotas.delete.assert_called_once_with(name="fakeid")
        mock_rest.view_policies.delete.assert_called_once_with(name="fakeid")

    def test_update_access_rules_wrong_proto(self):
        share = fake_share.fake_share(share_proto="SMB")
        access_rules = [
            {
                "access_level": "rw",
                "access_to": "127.0.0.1",
                "access_type": "ip"
            }
        ]
        res = self._driver.update_access(
            self._context,
            share,
            access_rules,
            None,
            None
        )
        self.assertIsNone(res)

    def test_update_access_add_rules_no_policy(self):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.one.return_value = None
        access_rules = [
            {
                "access_level": "rw",
                "access_to": "127.0.0.1",
                "access_type": "ip"
            }
        ]
        with patch.object(self._driver, "rest", mock_rest):
            with self.assertRaises(exception.ManilaException) as exc:
                self._driver.update_access(
                    self._context, share, access_rules, None, None
                )
            self.assertIn("Policy not found", str(exc.exception))

    @data(
        (["*"], ["10.10.10.1", "10.10.10.2"]),
        (["10.10.10.1", "10.10.10.2"], []),
        (["*"], []),
    )
    @unpack
    def test_update_access_add_rules(self, rw, ro):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.one.return_value = Bunch(
            id=1, nfs_read_write=rw, nfs_read_only=ro
        )
        access_rules = [
            {
                "access_level": "rw",
                "access_to": "127.0.0.1",
                "access_type": "ip"
            }
        ]
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.update_access(
                self._context,
                share,
                access_rules,
                None,
                None
            )

        expected_ro = set(ro)
        if rw == ["*"]:
            expected_rw = {"127.0.0.1"}
        else:
            expected_rw = set(["127.0.0.1"] + rw)
        kw = mock_rest.view_policies.update.call_args.kwargs
        self.assertEqual(kw["name"], "fakeid")
        self.assertSetEqual(set(kw["nfs_read_write"]), expected_rw)
        self.assertSetEqual(set(kw["nfs_read_only"]), expected_ro)
        self.assertEqual(kw["nfs_no_squash"], ["*"])
        self.assertEqual(kw["nfs_root_squash"], ["*"])

        # and the same for ro
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.one.return_value = Bunch(
            id=1, nfs_read_write=rw, nfs_read_only=ro
        )
        access_rules = [
            {
                "access_level": "ro",
                "access_to": "127.0.0.1",
                "access_type": "ip"
            }
        ]
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.update_access(
                self._context,
                share,
                access_rules,
                None,
                None
            )

        expected_rw = set(rw)
        if ro == ["*"]:
            expected_ro = {"127.0.0.1"}
        else:
            expected_ro = set(["127.0.0.1"] + ro)
        kw = mock_rest.view_policies.update.call_args.kwargs
        self.assertEqual(kw["name"], "fakeid")
        self.assertSetEqual(set(kw["nfs_read_write"]), expected_rw)
        self.assertSetEqual(set(kw["nfs_read_only"]), expected_ro)
        self.assertEqual(kw["nfs_no_squash"], ["*"])
        self.assertEqual(kw["nfs_root_squash"], ["*"])

    @data(
        (["*"], ["10.10.10.1", "10.10.10.2"]),
        (["10.10.10.1", "10.10.10.2"], []),
        (["*"], []),
    )
    @unpack
    def test_update_access_delete_rules(self, rw, ro):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.one.return_value = Bunch(
            id=1, nfs_read_write=rw, nfs_read_only=ro
        )
        delete_rules = [
            {
                "access_level": "rw",
                "access_to": "10.10.10.1",
                "access_type": "ip"
            }
        ]
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.update_access(
                self._context, share,
                None,
                None,
                delete_rules,
            )

        expected_ro = set(ro)
        if rw == ["*"]:
            expected_rw = set(rw)
        else:
            expected_rw = set([r for r in rw if r != "10.10.10.1"])
        kw = mock_rest.view_policies.update.call_args.kwargs
        self.assertEqual(kw["name"], "fakeid")
        self.assertSetEqual(set(kw["nfs_read_write"]), expected_rw)
        self.assertSetEqual(set(kw["nfs_read_only"]), expected_ro)
        self.assertEqual(kw["nfs_no_squash"], ["*"])
        self.assertEqual(kw["nfs_root_squash"], ["*"])

        # and the same for ro
        mock_rest = self._create_mocked_rest_api()
        mock_rest.view_policies.one.return_value = Bunch(
            id=1, nfs_read_write=rw, nfs_read_only=ro
        )
        delete_rules = [
            {
                "access_level": "ro",
                "access_to": "10.10.10.1",
                "access_type": "ip"
            }
        ]
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.update_access(
                self._context, share, None, None, delete_rules
            )

        expected_rw = set(rw)
        if ro == ["*"]:
            expected_ro = set(ro)
        else:
            expected_ro = set([r for r in ro if r != "10.10.10.1"])
        kw = mock_rest.view_policies.update.call_args.kwargs
        self.assertEqual(kw["name"], "fakeid")
        self.assertSetEqual(set(kw["nfs_read_write"]), expected_rw)
        self.assertSetEqual(set(kw["nfs_read_only"]), expected_ro)
        self.assertEqual(kw["nfs_no_squash"], ["*"])
        self.assertEqual(kw["nfs_root_squash"], ["*"])

    def test_resize_share_quota_not_found(self):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.quotas.one.return_value = None
        with patch.object(self._driver, "rest", mock_rest):
            with self.assertRaises(exception.ShareNotFound) as exc:
                self._driver.extend_share(share, 10000)
            self.assertIn("could not be found", str(exc.exception))

    def test_resize_share_ok(self):
        share = fake_share.fake_share(share_proto="NFS")
        mock_rest = self._create_mocked_rest_api()
        mock_rest.quotas.one.return_value = Bunch(
            id=1, used_effective_capacity=1073741824
        )
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.extend_share(share, 50)
            mock_rest.quotas.update.assert_called_with(
                1, hard_limit=53687091200
            )
            mock_rest.quotas.update.reset()
            self._driver.shrink_share(share, 20)
            mock_rest.quotas.update.assert_called_with(
                1, hard_limit=21474836480
            )

    def test_resize_share_exceeded_hard_limit(self):
        share = fake_share.fake_share(
            share_proto="NFS"
        )
        mock_rest = self._create_mocked_rest_api()
        mock_rest.quotas.one.return_value = Bunch(
            id=1, used_effective_capacity=10737418240
        )  # 10GB
        with patch.object(self._driver, "rest", mock_rest):
            with self.assertRaises(exception.ShareShrinkingPossibleDataLoss):
                self._driver.shrink_share(share, 9.7)
            self._driver.shrink_share(share, 10)

    def test_create_snapshot(self):
        snapshot = Bunch(name="fakesnap", share_instance_id="fakeid")
        mock_rest = self._create_mocked_rest_api()
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.create_snapshot(self._context, snapshot, None)
        mock_rest.snapshots.create.assert_called_once_with(
            path="/fake/manila-fakeid", name="fakesnap"
        )

    def test_delete_snapshot(self):
        snapshot = Bunch(name="fakesnap", share_instance_id="fakeid")
        mock_rest = self._create_mocked_rest_api()
        with patch.object(self._driver, "rest", mock_rest):
            self._driver.delete_snapshot(self._context, snapshot, None)
        mock_rest.snapshots.delete.assert_called_once_with(name="fakesnap")

    def test_network_allocation_number(self):
        self.assertEqual(self._driver.get_network_allocations_number(), 0)


class TestPolicyPayloadFromRules(unittest.TestCase):
    def test_policy_payload_from_rules_update(self):
        rules = [{"access_level": "rw", "access_to": "127.0.0.1"}]
        policy = MagicMock()
        policy.nfs_read_write = ["127.0.0.1"]
        policy.nfs_read_only = []
        result = policy_payload_from_rules(rules, policy, "update")
        self.assertEqual(
            result, {"nfs_read_write": ["127.0.0.1"], "nfs_read_only": []}
        )

    def test_policy_payload_from_rules_deny(self):
        rules = [{"access_level": "rw", "access_to": "127.0.0.1"}]
        policy = MagicMock()
        policy.nfs_read_write = ["127.0.0.1"]
        policy.nfs_read_only = []
        result = policy_payload_from_rules(rules, policy, "deny")
        self.assertEqual(result, {"nfs_read_write": [], "nfs_read_only": []})

    def test_policy_payload_from_rules_invalid(self):
        rules = [{"access_level": "rw", "access_to": "127.0.0.1"}]
        with self.assertRaises(ValueError):
            policy_payload_from_rules(rules, None, "invalid")

    @patch("socket.gethostbyname_ex")
    def test_policy_payload_from_rules_dns(self, mock_gethostbyname_ex):
        mock_gethostbyname_ex.return_value = (
            "hostname",
            [],
            ["192.168.1.1", "192.168.1.2"],
        )
        rules = [
            {"access_level": "rw", "access_to": "example.com"},
            {"access_level": "ro", "access_to": "example2.com"},
        ]
        policy = Bunch(nfs_read_write=["*"], nfs_read_only=["*"])
        result = policy_payload_from_rules(rules, policy, "update")
        self.assertSetEqual(
            set(result["nfs_read_write"]), {"192.168.1.1", "192.168.1.2"}
        )
        self.assertSetEqual(
            set(result["nfs_read_only"]), {"192.168.1.1", "192.168.1.2"}
        )

    def test_policy_payload_from_rules_dns_failed(self):
        rules = [
            {"access_level": "rw", "access_to": "example.testcom"},
            {"access_level": "ro", "access_to": "example2.testcom"},
        ]
        policy = Bunch(nfs_read_write=["*"], nfs_read_only=["*"])
        with patch("socket.gethostbyname_ex", side_effect=socket.gaierror):
            result = policy_payload_from_rules(rules, policy, "update")
        self.assertDictEqual(
            result, {"nfs_read_write": ["*"], "nfs_read_only": ["*"]}
        )


class TestValidateAccessRules(unittest.TestCase):
    def test_validate_access_rules_invalid_type(self):
        rules = [{"access_type": "INVALID", "access_level": "rw"}]
        with self.assertRaises(exception.InvalidShareAccess):
            validate_access_rules(rules)

    def test_validate_access_rules_invalid_level(self):
        rules = [{"access_type": "ip", "access_level": "INVALID"}]
        with self.assertRaises(exception.InvalidShareAccessLevel):
            validate_access_rules(rules)
