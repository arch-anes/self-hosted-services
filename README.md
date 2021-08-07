# Self-hosted services

## Create an inventory

### Static

```yml
# There must be a minimum of 3 controllers and the number must be odd for etcd to work
k3s_cluster:
  hosts:
    raspi:
      vpn_port: 3210
      vpn_ip: 10.10.10.1
      k3s_control_node: true
      labels:
        - dns=true
        - local=true
    big_manager:
      vpn_port: 3211
      vpn_ip: 10.10.10.2
      k3s_control_node: true
      labels:
        - nas=true
        - local=true
    small_manager:
      vpn_port: 3212
      vpn_ip: 10.10.10.3
      k3s_control_node: true
      labels:
        - public=true
        - local=true
    big_server:
      vpn_port: 3213
      vpn_ip: 10.10.10.4
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

## Deploy

Run `ansible-playbook setup_cluster.yml -i inventory_static.yml -i inventory_ec2.yml -e fqdn=example.com `
