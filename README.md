# Self-hosted services

## Create an inventory

### Static

```yml
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
    small_manager:
      # https://www.duckdns.org
      duckdns:
        token: duckdns_provided_token
        domain: example.duckdns.org
      cloudflare:
        zone: example.org
        domain: example.example.org
        token: cloudflare_api_token
      k3s_control_node: true
      labels:
        - public=true
        - local=true
    big_server:
      labels:
        - local=true
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

## Deploy

Run `ansible-playbook setup_cluster.yml -i inventory_static.yml -i inventory_ec2.yml -e fqdn=example.com -e tailscale_oauth_secret=some_generated_secret -e display_k8s_dashboard_password=true`


## Post-deployment step
To ensure no down time, make sure all the machines have key expiry disabled: https://tailscale.com/kb/1028/key-expiry#disabling-key-expiry.