# Self-hosted services

## Dependencies

-   docker-compose (remote only)
-   ansible (controller only)
-   openssh
-   rsync

## Create inventory

```yml
docker_swarm_manager:
  hosts:
    aws-small-instance:
      advertised_address: "42.42.42.42:2377"
      swarm_labels:
        - small
    raspi:
      advertised_address: "42.42.42.40:2378"
      swarm_labels:
        - local
        - small
    big-manager:
      advertised_address: "42.42.42.40:2379"
      swarm_labels:
        - local
        - big

docker_swarm_worker:
  hosts:
    big-worker:
      advertised_address: "42.42.42.40:2380"
      swarm_labels:
        - local
        - big
```

## Create custom config config

Copy `config.json.example` to `config.json` and fill it with your values

## Deploy

-   Run `ansible-galaxy install -r requirements.yml`
-   Run `ansible-playbook -e @config.json -i inventory.yml 0*.yml`
