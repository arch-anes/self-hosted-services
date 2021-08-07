# Self-hosted services

## Dependencies

-   docker-compose (remote only)
-   ansible (controller only)
-   openssh
-   rsync

## Create an inventory

### Static

```yml
docker_swarm_manager:
  hosts:
    raspi:
      vpn_port: 3211
      vpn_ip: 10.10.10.1
      swarm_labels:
        - local
        - dns
        - small
    big-manager:
      vpn_port: 3212
      vpn_ip: 10.10.10.2
      swarm_labels:
        - local
        - big

docker_swarm_worker:
  hosts:
    big-worker:
      vpn_port: 3213
      vpn_ip: 10.10.10.3
      swarm_labels:
        - local
        - big
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

## Create custom config config

Copy `config.json.example` to `config.json` and fill it with your values

## Deploy

Run `ansible-playbook -e @config.json -i inventory_static.yml -i inventory_ec2.yml setup_cluster.yml`
