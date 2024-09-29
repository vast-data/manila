====================================
Vastdata Share Driver
====================================

VASTData Share Driver can be used as a storage back end for the OpenStack Shared
File System service. Shares in the Shared File System service are
mapped 1:1 to VASTData volumes. Access is provided via NFS protocol
and IP-based authentication. The `VASTData <https://www.vastdata.com>`__
Manila driver uses the VASTData API service.

Supported shared filesystems
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The driver supports NFS shares.

Operations supported
~~~~~~~~~~~~~~~~~~~~
The driver supports NFS shares.

The following operations are supported:

-  Create a share.

-  Delete a share.

-  Allow share access.
    - IP access type is supported.
    - Read/write and read-only access are supported.

- Deny share access.

- Extend a share.

- Shrink a share.


Requirements
~~~~~~~~~~~~

-  Trash API must be enabled on VASTData cluster.

Driver options
~~~~~~~~~~~~~~

The following table contains the configuration options specific to the
share driver.

.. include:: ../../tables/manila-vastdata.inc


VASTData driver configuration example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following parameters shows a sample subset of the ``manila.conf`` file,
which configures VASTData manila backend
and the relevant ``[DEFAULT]`` options. A real
configuration would include additional ``[DEFAULT]`` options and additional
sections that are not discussed in this document:

.. code-block:: ini

   [DEFAULT]
   enabled_share_backends = vast
   enabled_share_protocols = NFS

   [vast]
   share_driver = manila.share.drivers.vastdata.driver.VASTShareDriver
   share_backend_name = vast
   driver_handles_share_servers = False
   snapshot_support = True
   vast_mgmt_host = {vms_ip}
   vast_mgmt_port = {vms_port}
   vast_mgmt_user = {mgmt_user}
   vast_mgmt_password = {mgmt_password}
   vast_vippool_name = {vip_pool}
   vast_root_export = {root_export}


Restart of ``manila-share`` service is needed for the configuration
changes to take effect.


Pre-configurations for share support
--------------------------------------------------

To create a file share you need to:

Create the share type:

    .. code-block:: console

        openstack share type create ${share_type_name} False \
            --extra-specs share_backend_name=${share_backend_name}

Create NFS share:

    .. code-block:: console

        openstack share create NFS ${size} --name ${share_name} --share-type ${share_type_name}

Pre-Configurations for Snapshot support
--------------------------------------------------

The following extra specifications need to be configured with share type.

- snapshot_support = True

For new share type, these extra specifications can be set directly when creating share type:

    .. code-block:: console

        openstack share type create ${share_type_name} false \
            --snapshot-support=true \
            --extra-specs share_backend_name=${share_backend_name}

Or you can update already existing share type with command:

    .. code-block:: console

        openstack share type set ${share_type_name} --extra-specs snapshot_support=True


To snapshot a share and create share from the snapshot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You need create a share from share type
that has extra specifications(snapshot_support=True).
Then snapshot the share with command:

    .. code-block:: console

        openstack share snapshot create ${source_share_name} --name ${target_snapshot_name}


Restrictions
------------

The VASTData driver has the following restrictions:

- Only IP access type is supported for NFS.


The :mod:`manila.share.drivers.vastdata.driver` Module
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: manila.share.drivers.vastdata.driver
    :noindex:
    :members:
    :undoc-members:
    :show-inheritance:
