MANILA
======

This is a fork of OpenStack's shared file system service: https://opendev.org/openstack/manila.git

It adds integration with VAST's storage system.


# Deployment Instructions

#### Prerequisites

First of all, you should have the appropriate version of Opestack installed.

2 versions of Openstack are currently supported:

- [Rocky](https://docs.openstack.org/rocky/) - corresponds to the `0.3` patch version
- [Stein](https://docs.openstack.org/stein/index.html) - corresponds to the `0.4` patch version
- [Train](https://docs.openstack.org/train/index.html) - corresponds to the `0.5` patch version


1. Download the appropriate patch file from the releases page and apply using the `patch` command:

```bash
VERSION=0.3  # Use appropriate version with accordance to description above.
wget https://github.com/vast-data/manila/releases/download/vast-$VERSION/vast-manila.patch -O /tmp/vast-manila.patch
cd /usr/lib/python2.7/site-packages/manila/
patch -p2 < /tmp/vast-manila.patch
```

2. Edit your `/etc/manila/manila.conf` file:
```
enabled_share_backends = vast
...

[vast]
share_driver = manila.share.drivers.vastdata.VASTShareDriver
share_backend_name = vast
snapshot_support = True
driver_handles_share_servers = False
vast_mgmt_host = <VAST MGMT HOST>
vast_mgmt_user = <VAST MGMT USER>
vast_mgmt_password = <VAST MGMT PASSWORD>
vast_vippool_name = <VAST VIP POOL NAME>
vast_root_export = <VAST EXPORT FOR MANILA SHARES>
```

3. Restart the Manila services:
```bash
systemctl restart openstack-manila-share.service
```

4. Create a new Share Type:
```bash
manila type-create vast \
    false  \
    --snapshot_support=true \
    --extra-specs share_backend_name=vast
```

5. You may create shares and snapshots:
```bash
# create a new share named "vast-share1" with a 5Gb quota
manila create --name vast-share1 --share-type vast nfs 5

# show the export locations
manila show vast-share1

# create a snapshot
manila snapshot-create vast-share1 --name vast-snap1

# create access rules
manila access-allow vast-share1 ip 1.1.1.3 --access_level ro
manila access-allow vast-share1 ip 1.1.1.4 --access_level rw
manila access-list vast-share1

# delete share and snapshot
manila delete vast-share1
manila snapshot-delete vast-snap1
```

## Troubleshooting

* Logs are available via journalctl:
```
  journalctl  -u   devstack@m-shr.service
  journalctl  -u   devstack@m-api.service
  journalctl  -u   devstack@m-sch
  journalctl  -u   devstack@m-shr
```
* In some cases it might be necessary to restart other manila services:
    ```bash
    systemctl restart openstack-manila-scheduler.service
    systemctl restart openstack-manila-api.service
    ```
