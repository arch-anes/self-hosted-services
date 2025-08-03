# Self-hosted services

## Setup tailscale
1. Create an account at https://login.tailscale.com.
1. Add the following ACL rule at https://login.tailscale.com/admin/acls/file:
   ```
    "tagOwners": {
      "tag:ansible": ["autogroup:admin", "autogroup:owner"],
    },
   ```
1. Create an OAuth client at https://login.tailscale.com/admin/settings/oauth:
  1. Enable the Write permission for Device/Core, and add the "tag:ansible" tag.
  1. Enable the Write permission for Keys/Auth Keys, and add the "tag:ansible" tag.
  1. Save and write down the OAuth client secret.

## Setup ZFS
1. SSH into each host that supports ZFS.
1. Create a pool
   ```
    sudo zpool create -m /zfs-pool-dummy-mountpoint-do-not-use storage mirror SOME_DEVICE_1 SOME_DEVICE_2
    sudo zfs set compression=lz4 storage
    sudo zfs set atime=off storage
   ```
1. Create an encrypted dataset:
   ```
    sudo openssl rand -out /root/keyfile-zfs 32
    sudo zfs create -o encryption=on -o keylocation=file:///root/keyfile-zfs -o keyformat=raw -o mountpoint=/storage storage/encrypted
   ```
1. Create a block volume for OpenEBS' Mayastor
   ```
   sudo zfs create -V 100G storage/encrypted/mayastor
   ```

## Create an inventory

### Static

```yml
all:
  vars:
    k3s_control_node: false
    skip_system_setup: false
    skip_firewall_setup: false
    skip_vpn_setup: false
    skip_k8s_setup: false
    skip_binary_update: false
    manifest_only_setup: false
    display_k8s_dashboard_password: false
    timezone: America/Vancouver
    tailscale_oauth_secret: "some_secret"
    chartValuesOverrides:
      fqdn: "example.com"
      storageLocation: /storage
      # Optional: disable unwanted applications
      applications:
        crowdsec:
          enabled: false
# There must be a minimum of 3 controllers and the number must be odd for etcd to work
k3s_cluster:
  hosts:
    raspi:
      k3s_control_node: true
      labels:
        - local=true
    big_manager:
      k3s_control_node: true
      labels:
        - nas=true
        - local=true
        - openebs.io/engine=mayastor
    small_manager:
      k3s_control_node: true
      labels:
        - public=true
        - local=true
        - openebs.io/engine=mayastor
    big_server:
      labels:
        - local=true
        - openebs.io/engine=mayastor
# Optional
headscale:
  hosts:
    headscale_control_server: {}
```

### Dynamic (AWS)

```yml
plugin: aws_ec2
regions:
  - us-east-1
  - us-east-2
filters:
  instance-state-name: running
  tag:Category:
    - home-cloud
```


## Deploy

Run `ansible-playbook setup_cluster.yml -i inventory_static.yml -i inventory_ec2.yml`


## Post-deployment step
To ensure no down time, make sure all the machines have key expiry disabled: https://tailscale.com/kb/1028/key-expiry#disabling-key-expiry.

## Advanced use-cases

### Load balancing
In a typical home network setup, when HTTP(S) ports are forwarded to a specific machine, the entire service becomes unavailable if that machine goes offline. However, if your router supports OpenWRT (such as the GL-MT6000), you can install HAProxy to address this issue. For optimal security and high availability, configure the proxy as follows:

`/etc/haproxy.cfg`:
```
global
    log /dev/log local0
    log-tag HAProxy
    maxconn 32000
    ulimit-n 65535
    uid 0
    gid 0
    nosplice
    daemon

defaults
    log global
    mode tcp
    timeout connect 5s
    timeout client 30s
    timeout server 30s
    option redispatch
    retries 3
    option log-health-checks
    option dontlognull
    option dontlog-normal

frontend http-in
    bind :9080
    mode tcp
    default_backend http-servers

frontend https-in
    bind :9443
    mode tcp
    default_backend https-servers

backend http-servers
    mode tcp
    balance roundrobin
    option httpchk
    http-check connect port 8080
    http-check send meth GET uri /ping
    default-server inter 3s fall 3 rise 2
    server s1 192.168.1.11:80 send-proxy check
    server s2 192.168.1.12:80 send-proxy check
    server s3 192.168.1.13:80 send-proxy check

backend https-servers
    mode tcp
    balance roundrobin
    option httpchk
    http-check connect port 8080
    http-check send meth GET uri /ping
    default-server inter 3s fall 3 rise 2
    server s1 192.168.1.11:443 send-proxy check
    server s2 192.168.1.12:443 send-proxy check
    server s3 192.168.1.13:443 send-proxy check
```

With this configuration, all incoming HTTP(S) traffic must now flow through the gateway ports 9080/9443 where HAProxy is installed. This is because the router forwards traffic to the HAProxy instance, which then distributes it to the backend servers. This setup ensures that even if one server goes down, the service remains available, as HAProxy will route traffic to the remaining operational servers.
