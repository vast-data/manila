..
    Copyright 2020 VAST Data Inc.
    All Rights Reserved.

    Licensed under the Apache License, Version 2.0 (the "License"); you may
    not use this file except in compliance with the License. You may obtain
    a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
    License for the specific language governing permissions and limitations
    under the License.

====================================
Vastdata Driver for OpenStack Manila
====================================

The `Vastdata <https://www.vastdata.com>`__ Manila driver
provides NFS and CIFS shared file systems to Openstack.

Requirements
------------

- Vastdata cluster must be configured with Trash API enabled. # TODO describe; add more requirements (if needed)

Supported Operations
--------------------

The following operations are supported on an Vastdata cluster:

* Create NFS Share
* Delete NFS Share
* Allow NFS Share access
* Deny NFS Share access
* Create snapshot
* Delete snapshot
* Extend share

Backend Configuration
---------------------

The following parameters need to be configured in the manila configuration file
for the Vastdata driver:

* share_driver = manila.share.drivers.vastdata.VASTShareDriver
* emc_share_backend = vast
* vast_mgmt_host = <IP address of Vastdata cluster>
* vast_mgmt_port = <port to use for Vastdata cluster (optional)>
* vast_mgmt_user = <username>
* vast_mgmt_password = <password>
* vast_vippool_name = # TODO describe
* vast_root_export = # TODO describe


Restart of :term:`manila-share` service is needed for the configuration changes to take
effect.

Restrictions
------------

The Vastdata driver has the following restrictions:

- Only IP access type is supported for NFS.


The :mod:`manila.share.drivers.vastdata` Module
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: manila.share.drivers.dell_emc.driver
    :noindex:
    :members:
    :undoc-members:
    :show-inheritance:
