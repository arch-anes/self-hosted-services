# Self-hosted services

## Dependencies

-   ansible (controller only)
-   openssh
-   rsync

## Create inventory

```
# There must be a minimum of 3 controllers and the number must be odd for etcd to work
[k3s_cluster]
aws-instance k3s_control_node=true
raspi2 k3s_control_node=true
raspi4 k3s_control_node=true
big_server
big_server_2
```

## Create custom config config

Copy `config.json.example` to `config.json` and fill it with your values

## Deploy

-   Run `ansible-galaxy install -r requirements.yml`
-   Run `ansible-playbook -e @config.json 0*.yml`
